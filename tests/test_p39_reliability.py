import asyncio,time,threading
from decimal import Decimal as D
from types import SimpleNamespace as N
import pytest
from sqlalchemy import select,text,delete
from engine.adapters.base import Batch
from engine.models import Asset,Instrument,RawEvent,Candle,MarketState,UserPreference
from engine.repository import save_batch
from engine.p38 import jobs
from engine.pd2.book_clock import fresh
from engine.pd2.decision import decide
from engine.pd2.outcomes import evaluate_path
from engine.pd2.write_queue import Writer
from test_p25_signals import isolated

def candle_batch(s,received=100,price='10',quote='100',closed=True):
    iid='binance:perp:P39TESTUSDT'
    if not s.get(Asset,'P39TEST'):
        s.add(Asset(id='P39TEST',symbol='P39TEST',known_at=0));s.flush()
        s.add(Instrument(id=iid,asset_id='P39TEST',venue='binance',native_symbol='P39TESTUSDT',market_type='linear_perpetual',quote_asset='USDT',spec={}));s.flush()
    ref=f'p39raw:{received}:{price}:{quote}:{closed}'
    batch=Batch('binance',candle_only=True)
    batch.raw=[dict(id=ref,source='binance',endpoint='test',received_at=received,payload={'price':price})]
    batch.candles=[dict(instrument_id=iid,resolution='60',open_time=0,close_time=3600000,is_closed=closed,open=price,high=price,low=price,close=price,base_volume='10',quote_volume=quote,received_at=received,raw_ref=ref)]
    return batch

@pytest.mark.parametrize('scenario',['older_closed','forming_after_closed','duplicate','quote_revision','price_revision','raw_immutable'])
def test_atomic_fact_contract(isolated,scenario):
    with isolated.begin() as s:original=candle_batch(s)
    save_batch(original)
    if scenario=='older_closed':
        with isolated.begin() as s:batch=candle_batch(s,90,'9')
    elif scenario=='forming_after_closed':
        with isolated.begin() as s:batch=candle_batch(s,110,'11',closed=False)
    elif scenario=='quote_revision':
        with isolated.begin() as s:batch=candle_batch(s,110,quote='120')
    elif scenario=='price_revision':
        with isolated.begin() as s:batch=candle_batch(s,110,'11')
    else:batch=original
    if scenario=='raw_immutable':batch.raw[0]['payload']={'changed':True}
    save_batch(batch)
    with isolated() as s:
        row=s.get(Candle,('binance:perp:P39TESTUSDT','60',0))
        assert row.is_closed
        assert row.revision==(2 if scenario in ('quote_revision','price_revision') else 1)
        assert row.close==D('11' if scenario=='price_revision' else '10')
        assert s.get(RawEvent,original.raw[0]['id']).payload=={'price':'10'}

def test_task_dedup_and_quote_does_not_rebuild_structure(isolated):
    at=int(time.time()*1000)
    with isolated.begin() as s:
        candle_batch(s);s.add(MarketState(instrument_id='binance:perp:P39TESTUSDT',as_of=at,payload={'price':'10'}))
    iid='binance:perp:P39TESTUSDT';a=jobs.dependency_versions([iid],at)[iid]
    with isolated.begin() as s:s.get(MarketState,iid).as_of=at+1000
    assert jobs.dependency_versions([iid],at+3000)[iid]==a
    assert jobs.dependency_versions([iid],at,work_kind='entry')[iid]!=a
    jobs.submit([iid],at=at);lease=jobs.take(at)
    jobs.submit([iid],at=at+3000);jobs.finish(lease,at=at+4000)
    assert jobs.take(at+4000) is None

def test_ordinary_market_cannot_enter_deep_queue(isolated):
    jobs.submit(['ordinary'],reason='closed_candle');assert jobs.take() is None

@pytest.mark.parametrize('latency,age,expected',[(100,500,True),(1200,500,False),(100,11000,True),(100,90001,False),(None,500,False)])
def test_spot_receive_provenance(latency,age,expected):
    book=dict(venue='binance_spot',source_time_kind='snapshot_received',received_at=20000-age,observed_at=None,last_update_id=123,roundtrip_ms=latency)
    assert fresh(book,20000)==expected
    book['venue']='binance_alpha';assert not fresh(book,20000)

def test_expiry_precedes_quote_delay():
    action=decide({'expires_at':100,'trigger':True,'entry_zone':['9','11'],'protection':'8'},'structure_confirmed',None,101)
    assert action['code']=='expired' and 'quote_stale' in action['reason_codes']

def test_cancelled_fact_writer_drains_all_accepted():
    async def run():
        committed=[]
        async def write(item):await asyncio.sleep(.01);committed.append(item)
        writer=Writer(write,8);writer.task=asyncio.create_task(writer.run())
        for n in range(5):await writer.put(n)
        closing=asyncio.create_task(writer.close());await asyncio.sleep(.001);closing.cancel()
        with pytest.raises(asyncio.CancelledError):await closing
        assert committed==list(range(5));assert writer.committed==writer.accepted==5
    asyncio.run(run())

def test_thread_cancel_does_not_release_owner_before_thread_exits():
    from engine.p38.lifecycle import thread
    async def run():
        started=threading.Event();release=threading.Event();finished=[]
        def work():started.set();release.wait(2);finished.append(True)
        task=asyncio.create_task(thread(work));await asyncio.to_thread(started.wait,1)
        task.cancel();await asyncio.sleep(.01);assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):await task
        assert finished==[True]
    asyncio.run(run())

def test_outcome_missing_first_and_tail_are_repairable():
    bars=[dict(open_time=n*3600000,close_time=(n+1)*3600000,open='10',low='9.5',high='10.5',close='10') for n in range(1,4)]
    assert evaluate_path(bars[1:],'10','12','8',0,14400000)['state']=='data_gap'
    assert evaluate_path(bars[:1],'10','12','8',0,14400000)['state']=='incomplete_horizon'
    assert evaluate_path(bars,'10','12','8',0,14400000)['state']=='expired'

def test_lost_role_prevents_commit():
    from engine.db import engine,BoundedSession
    from engine.pd2.scheduling import role_lock
    import engine.db as db
    assert engine.url.database.startswith('erji_p36_check_')
    owner=role_lock('p39-test-owner');db.role_owner=owner
    try:
        with BoundedSession(engine) as s:
            s.add(UserPreference(key='p39-role-marker',updated_at=1,payload={}));s.commit()
        with engine.connect() as c:c.execute(text('SELECT pg_terminate_backend(:pid)'),{'pid':owner.pid});c.commit()
        with pytest.raises(Exception):
            with BoundedSession(engine) as s:
                s.add(UserPreference(key='p39-stale-owner',updated_at=1,payload={}));s.commit()
        with engine.connect() as c:assert c.execute(text("SELECT count(*) FROM user_preferences WHERE key='p39-stale-owner'")).scalar()==0
    finally:
        db.role_owner=None;owner.close()
        with engine.begin() as c:c.execute(delete(UserPreference).where(UserPreference.key.in_(['p39-role-marker','p39-stale-owner'])))

def seed_plan(s,at,expired=False,p33=False):
    from engine.models import Campaign,Episode,Plan,SignalSnapshot,DataCapability
    from engine.contracts import RuntimeConfig
    candle_batch(s);iid='binance:perp:P39TESTUSDT'
    s.add(MarketState(instrument_id=iid,as_of=at,payload={'price':'10','price_kind':'last'}))
    s.add(Campaign(id='p39-c',asset_id='P39TEST',reference_instrument_id=iid,first_seen_at=at-10000,first_seen_price=D('9'),state='active'));s.flush()
    s.add(Episode(id='p39-e',campaign_id='p39-c',strategy_id='probe',phase='structure_confirmed',rule_version='p33-test' if p33 else 'p39-test',evidence={}));s.flush()
    cfg=RuntimeConfig().model_dump();expiry=at-1 if expired else at+60000
    evidence={'entry_zone':['9.99','10.01'],'protection':'8','expires_at':expiry,'trigger':{'close_time':at-10000}}
    payload={'config':cfg,'evidence':evidence,'original_protection':'8','original_t1':{'low':'15'},'template':'confirmed_breakout','opportunity':{}}
    if p33:payload.update(p33=True)
    s.add(Plan(id='p39-p',episode_id='p39-e',execution_instrument_id=iid,revision=1,expires_at=expiry,payload=payload));s.flush()
    snap={'id':'p39-old','plan_id':'p39-p','episode_id':'p39-e','asset_id':'P39TEST','instrument_id':iid,'as_of':at-10000,'rule_version':'test','phase':'structure_confirmed','action':{'code':'participate','label':'建议买入','reason':'old'},'current_protection':'8','evidence':evidence,'opportunity_plan':{},'market':{'price':'9','as_of':at-10000},'trade_thesis':{'headline':'old'},'timeframe_assessments':[{'timeframe':tf,'status':'fresh','closed_at':at} for tf in ('60','240')],'candles_at_snapshot':{'60':[{'close':'9'}]}}
    snap.update(first_seen_at=at-10000,first_seen_price='9',phase_label='结构确认',execution=None)
    snap['market'].update(instrument_id=iid,asset_id='P39TEST',venue='binance',symbol='P39TEST',quote_asset='USDT')
    s.add(SignalSnapshot(id='p39-old',plan_id='p39-p',as_of=at-10000,payload=snap))
    book={'asks':[['10','1000']],'bids':[['9.99','1000']],'observed_at':at,'received_at':at,'scope':'execution_market'}
    s.add(DataCapability(key='binance:execution_book:'+iid,provider='binance',capability='execution_book',subject=iid,status='fresh',received_at=at,observed_at=at,expires_at=at+10000,payload=book))
    s.merge(DataCapability(key='binance:fx:USDTUSD',provider='binance',capability='fx',subject='USDTUSD',status='fresh',received_at=at,observed_at=at,expires_at=at+3600000,payload={'symbol':'USDTUSD','index':'1'}))
    return iid,snap

@pytest.mark.parametrize('expired,protection_hit',[(False,False),(True,False),(True,True)])
def test_current_refresh_frozen_history_and_expired_protection(isolated,monkeypatch,expired,protection_hit):
    from engine.models import SignalSnapshot,Plan
    from engine.pd2 import service
    at=int(time.time()*1000)
    with isolated.begin() as s:
        iid,old=seed_plan(s,at,expired)
        if protection_hit:s.get(MarketState,iid).payload={'price':'7','price_kind':'last'}
    monkeypatch.setattr(service,'now_ms',lambda:at)
    assert service.refresh_current([iid])['snapshots']==1
    with isolated() as s:
        latest=s.scalar(select(SignalSnapshot).order_by(SignalSnapshot.as_of.desc()).limit(1)).payload
        assert latest['action']['code']==('ended' if protection_hit else 'expired' if expired else 'participate')
        assert s.get(SignalSnapshot,'p39-old').payload==old
        assert latest['candles_at_snapshot']==old['candles_at_snapshot']
        assert s.get(Plan,'p39-p').payload['original_protection']=='8'
        assert latest['market']['price']==('7' if protection_hit else '10')
        if expired:assert latest['valid_until']<=at

def test_p33_current_cannot_bypass_missing_original_checkpoint(isolated,monkeypatch):
    from engine.models import SignalSnapshot
    from engine.pd2 import service
    at=int(time.time()*1000)
    with isolated.begin() as s:iid,old=seed_plan(s,at,p33=True)
    monkeypatch.setattr(service,'now_ms',lambda:at)
    service.refresh_current([iid])
    with isolated() as s:
        current=s.scalar(select(SignalSnapshot).order_by(SignalSnapshot.as_of.desc()).limit(1)).payload
        assert current['action']['code']=='paused'
        assert 'tracking_checkpoint_missing' in current['action']['reason_codes']
        assert s.get(SignalSnapshot,'p39-old').payload==old

@pytest.mark.parametrize('change',['expire','disable','revoke','lease_changed'])
def test_notification_each_send_rechecks_batch_without_network(isolated,monkeypatch,change):
    from engine.models import NotificationTarget,Outbox,SignalSnapshot
    from engine.pd2 import notifications
    from engine import repository
    from engine.contracts import RuntimeConfig
    from engine.repository import emit
    at=int(time.time()*1000);clock=[at];cfg=RuntimeConfig();cfg.notifications.enabled=True
    with isolated.begin() as s:
        iid,_=seed_plan(s,at)
        snap=s.get(SignalSnapshot,'p39-old');snap.as_of=at;snap.payload={**snap.payload,'as_of':at,'valid_until':at+5000,'execution':{'received_at':at}}
        event_id=emit(s,'signal_update',{})
        for n in range(2):
            tid='p39target'+str(n);s.add(NotificationTarget(id=tid,chat_id=tid,enabled=True,authorized_at=at,subscriptions={}))
            from engine.transaction_events import add_outbox
            add_outbox(s,Outbox(id='p39out'+str(n),dedupe_key=tid,state='pending',expires_at=at+60000,next_attempt_at=at-1,payload={'target_id':tid,'snapshot_id':snap.id}),event_id)
    monkeypatch.setattr(notifications,'now_ms',lambda:clock[0]);monkeypatch.setattr(notifications,'read_key',lambda _: 'fake-local-only')
    monkeypatch.setattr(repository,'runtime_config',lambda *args:cfg)
    sent=[]
    async def fake_send(client,token,chat,message):
        sent.append(chat)
        with isolated.begin() as s:
            other=s.scalar(select(Outbox).where(Outbox.payload['target_id'].as_string()!=chat))
            if change=='expire':clock[0]+=11000
            elif change=='disable':cfg.notifications.enabled=False
            elif change=='revoke':s.get(NotificationTarget,other.payload['target_id']).authorized_at=0
            else:other.payload={**other.payload,'lease_token':'replacement-owner'}
        return 'sent',{'message_id':1}
    monkeypatch.setattr(notifications,'send_text',fake_send)
    assert asyncio.run(notifications.dispatch_once())['sent']==1
    assert len(sent)==1
    with isolated() as s:
        states=[r.state for r in s.scalars(select(Outbox))]
        assert sorted(states)==sorted(['sent','sending' if change=='lease_changed' else 'expired'])

def test_priority_has_aged_background_floor(isolated):
    from engine.models import Task
    at=int(time.time()*1000)
    jobs.submit(['priority-protect'],work_kind='protect',at=at)
    jobs.submit(['recent-structure'],at=at)
    first=jobs.take(at);assert first['work_kind']=='protect';jobs.finish(first,at=at)
    assert jobs.take(at+60001)['instrument_id']=='recent-structure'

def test_resource_acquisition_cancel_closes_late_connection():
    from engine.p38.lifecycle import acquire_thread
    async def scenario():
        started=threading.Event();release=threading.Event();cleaned=[]
        def connect():started.set();release.wait(3);return 'connection'
        task=asyncio.create_task(acquire_thread(connect,cleaned.append))
        await asyncio.to_thread(started.wait,1);task.cancel();release.set()
        with pytest.raises(asyncio.CancelledError):await task
        assert cleaned==['connection']
    asyncio.run(scenario())

def test_committed_window_rebuilds_on_quote_revision(isolated):
    from engine.p38 import windows
    with isolated.begin() as s:batch=candle_batch(s)
    save_batch(batch)
    with isolated() as s:
        old=windows.read(s,'binance:perp:P39TESTUSDT','60',4000000)
        old[0]['close']='bad-local-mutation'
        assert windows.read(s,'binance:perp:P39TESTUSDT','60',4000000)[0]['close']==D('10')
    with isolated.begin() as s:revised=candle_batch(s,110,quote='120')
    save_batch(revised)
    with isolated() as s:
        new=windows.read(s,'binance:perp:P39TESTUSDT','60',4000000)
        assert new[0]['quote_volume']==D('120') and new[0]['revision']==2

def test_background_precompute_keeps_same_classification():
    from engine.p37.detector import Detector,DEFAULTS
    d=Detector();d.config={**DEFAULTS,'breadth_min':2}
    for iid in ['binance:perp:BTCUSDT','x','y']:d.catalog[iid]=dict(venue='binance',asset_id=iid,liquidity=2000000)
    facts={k:dict(changes={'5':2,'15':None},reasons=[dict(kind='price',direction='up',window=5,value=3)],rvol=1,oi_change=0) for k in d.catalog}
    background=d.backgrounds(facts)
    assert all(d.classify(k,facts[k],facts)==d.classify(k,facts[k],facts,background) for k in facts)
    assert background[2]['binance']['coverage']['5']=={'available':3,'eligible':3}

def test_managed_plan_keeps_protection_without_deep_admission(isolated,monkeypatch):
    from engine.models import Task
    at=int(time.time()*1000)
    with isolated.begin() as s:iid,_=seed_plan(s,at)
    jobs.submit([iid],reason='closed_candle',at=at)
    assert jobs.take(at) is None
    jobs.submit([iid],reason='managed_risk',at=at,work_kind='protect')
    lease=jobs.take(at);assert lease['work_kind']=='protect'
    class NoNetwork:
        async def collect(self,*args):raise AssertionError('Protection must not collect')
    from engine.pd2 import service
    monkeypatch.setattr(service,'now_ms',lambda:at)
    asyncio.run(jobs.execute(lease,NoNetwork()))
    with isolated() as s:
        assert s.get(Task,lease['id']).state=='complete'
    # A slot alone is no longer demand. Recover the real plan into T2 and
    # admit it through the same bounded selector as an observation.
    from engine.p39.pipeline import select_work
    from engine.p37.detector import DEFAULTS
    assert select_work(at,50,'structure',DEFAULTS)[0]
    jobs.submit([iid],reason='closed_candle',at=at)
    assert jobs.take(at)['work_kind']=='structure'

def test_worker_role_loss_cancels_body_and_drains_late_thread(monkeypatch):
    from services.worker import main
    from engine.pd2 import scheduling
    import engine.db as db
    from engine.p38.lifecycle import thread
    real_sleep=asyncio.sleep;started=threading.Event();release=threading.Event();events=[]
    class Owner:
        lost=False
        def check(self):
            self.lost=True;release.set();raise RuntimeError('worker_role_lost')
        def close(self):events.append('closed')
    owner=Owner()
    monkeypatch.setattr(scheduling,'role_lock',lambda _:owner)
    async def short_sleep(seconds):
        await real_sleep(.02 if seconds==2 else seconds)
    monkeypatch.setattr(main.asyncio,'sleep',short_sleep)
    async def body(role,once):
        def late():
            assert db.owner_context.get() is owner
            started.set();release.wait(1)
            events.append('thread_lost' if owner.lost else 'thread_live')
        try:
            await thread(late)
            await real_sleep(2)
        finally:events.append('body_drained')
    monkeypatch.setattr(main,'run_locked',body)
    with pytest.raises(RuntimeError,match='worker_role_lost'):asyncio.run(main.run('test'))
    assert started.is_set() and events==['thread_lost','body_drained','closed']
    assert db.role_owner is None and db.owner_context.get() is None

def test_outcome_rechecks_database_after_first_and_tail_backfill(isolated,monkeypatch):
    from engine.models import Plan,PlanOutcome,SignalSnapshot
    from engine.pd2 import outcomes
    h=3600000;now=5*h
    with isolated.begin() as s:
        iid,_=seed_plan(s,now)
        p=s.get(Plan,'p39-p');p.payload={**p.payload,'config':{**p.payload['config'],'validation':{'horizons_hours':[4]}}}
        snap=s.get(SignalSnapshot,'p39-old');snap.as_of=0
        s.add(RawEvent(id='p39-outcome-raw',source='test',endpoint='synthetic',received_at=now,payload={}))
        s.flush()
        for n in (2,3):
            s.add(Candle(instrument_id=iid,resolution='60',open_time=n*h,close_time=(n+1)*h,is_closed=True,open=D('10'),high=D('10.5'),low=D('9.5'),close=D('10'),base_volume=D('1'),quote_volume=D('10'),received_at=now,revision=1,raw_ref='p39-outcome-raw'))
    monkeypatch.setattr(outcomes,'now_ms',lambda:now)
    outcomes.update_outcomes()
    with isolated() as s:assert s.get(PlanOutcome,('p39-p',4)).state=='data_gap'
    with isolated.begin() as s:
        s.add(Candle(instrument_id=iid,resolution='60',open_time=h,close_time=2*h,is_closed=True,open=D('10'),high=D('10.5'),low=D('9.5'),close=D('10'),received_at=now,revision=1,raw_ref='p39-outcome-raw'))
    outcomes.update_outcomes()
    with isolated() as s:assert s.get(PlanOutcome,('p39-p',4)).state=='expired'
    # A newly discovered missing tail must also recover when its revision lands.
    with isolated.begin() as s:s.delete(s.get(Candle,(iid,'60',3*h)))
    outcomes.update_outcomes()
    with isolated() as s:assert s.get(PlanOutcome,('p39-p',4)).state=='incomplete_horizon'
    with isolated.begin() as s:
        s.add(Candle(instrument_id=iid,resolution='60',open_time=3*h,close_time=4*h,is_closed=True,open=D('10'),high=D('10.5'),low=D('9.5'),close=D('10'),received_at=now,revision=2,raw_ref='p39-outcome-raw'))
    outcomes.update_outcomes()
    with isolated() as s:assert s.get(PlanOutcome,('p39-p',4)).state=='expired'

@pytest.mark.parametrize('gap',[False,True])
def test_p33_checkpoint_refresh_preserves_original_dependency_gates(isolated,monkeypatch,gap):
    from copy import deepcopy
    from test_p33_completion import event
    from test_p33_patterns import NOW
    from engine.models import Plan,Episode,SignalSnapshot
    from engine.pd2 import service
    original,bars=event()
    with isolated.begin() as s:
        iid,old=seed_plan(s,NOW,p33=True)
        original['initial']['market_id']=iid
        original['plan']['market_id']=iid
        original['initial']['expires_at']=NOW+60000
        original['plan']['expires_at']=NOW+60000
        s.get(MarketState,iid).payload={'price':'12.8','price_kind':'last'}
        s.add(UserPreference(key='p33_shadow:'+iid,updated_at=NOW-1,payload={'active_events':{'p39-e':original}}))
        plan=s.get(Plan,'p39-p');frozen=deepcopy(plan.payload)
    if gap:bars['240'].pop(-10)
    monkeypatch.setattr(service,'load_bars',lambda *args:bars)
    monkeypatch.setattr(service,'now_ms',lambda:NOW)
    service.refresh_current([iid])
    with isolated() as s:
        latest=s.scalar(select(SignalSnapshot).order_by(SignalSnapshot.as_of.desc()).limit(1)).payload
        qualification=latest['research_qualification']
        assert qualification['dependency_data']['240']['status']==('unavailable' if gap else 'fresh')
        assert latest['action']['code']==('paused' if gap else 'no_chase')
        assert ('dependency_candles_unavailable' in latest['action']['reason_codes'])==gap
        assert s.get(SignalSnapshot,'p39-old').payload==old
        assert s.get(Plan,'p39-p').payload==frozen

def test_concurrent_candle_revision_cannot_restore_older_fact():
    from concurrent.futures import ThreadPoolExecutor
    from engine.db import engine,Session
    assert engine.url.database.startswith('erji_p36_check_')
    iid='binance:perp:P39RACEUSDT'
    with Session.begin() as s:
        s.add(Asset(id='P39RACE',symbol='P39RACE',known_at=0));s.flush()
        s.add(Instrument(id=iid,asset_id='P39RACE',venue='binance',native_symbol='P39RACEUSDT',market_type='linear_perpetual',quote_asset='USDT',spec={}));s.flush()
    def batch(stamp):
        b=Batch('binance',candle_only=True);ref='p39-race-'+str(stamp)
        b.raw=[dict(id=ref,source='test',endpoint='synthetic-race',received_at=stamp,payload={'stamp':stamp})]
        b.candles=[dict(instrument_id=iid,resolution='60',open_time=0,close_time=3600000,is_closed=True,open=str(stamp),high=str(stamp),low=str(stamp),close=str(stamp),base_volume='1',quote_volume='1',received_at=stamp,raw_ref=ref)]
        return b
    save_batch(batch(100));barrier=threading.Barrier(2)
    def write(stamp):barrier.wait(2);save_batch(batch(stamp))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(write,stamp) for stamp in (110,120)]
        for future in futures:future.result(timeout=5)
    with Session() as s:
        fact=s.get(Candle,(iid,'60',0))
        assert fact.received_at==120 and fact.close==D('120') and fact.is_closed
        assert fact.revision in (2,3)
        assert all(s.get(RawEvent,'p39-race-'+str(stamp)).payload=={'stamp':stamp} for stamp in (100,110,120))

def test_long_heartbeat_names_preserve_distinct_full_identities(isolated):
    from engine.repository import heartbeat
    from engine.models import WorkerHeartbeat
    names=['discovery_receiver:binance:binance:perp:'+symbol+'USDT' for symbol in ('AAA','BBB')]
    for role in names:heartbeat(role,'connected',{'markets':128})
    with isolated() as s:
        rows=[r for r in s.scalars(select(WorkerHeartbeat)) if r.details.get('role_name') in names]
        assert len(rows)==2 and len({r.role for r in rows})==2
        assert all(len(r.role)<=40 and r.state=='connected' for r in rows)
        assert {r.details['role_name'] for r in rows}==set(names)

def test_multirow_funding_conflicts_use_correlated_source_clock(isolated):
    from engine.models import FundingPoint
    with isolated.begin() as s:initial=candle_batch(s)
    save_batch(initial);iid='binance:perp:P39TESTUSDT'
    def update(stamp,rates):
        ref='p39-funding-'+str(stamp);b=Batch('binance',candle_only=True)
        b.raw=[dict(id=ref,source='test',endpoint='funding-fixture',received_at=stamp,payload={})]
        b.funding=[dict(instrument_id=iid,settled_at=n+1,rate=rate,interval_seconds=28800,raw_ref=ref) for n,rate in enumerate(rates)]
        save_batch(b)
    update(100,['.0001','.0002']);update(200,['.0003','.0004']);update(150,['.0005','.0006'])
    with isolated() as s:
        assert [s.get(FundingPoint,(iid,n)).rate for n in (1,2)]==[D('.0003'),D('.0004')]
        assert all(s.get(FundingPoint,(iid,n)).raw_ref=='p39-funding-200' for n in (1,2))
