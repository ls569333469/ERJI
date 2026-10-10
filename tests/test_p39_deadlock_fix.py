"""Real concurrent PostgreSQL transactions and bounded local failure recovery."""
import asyncio,json,threading,time,uuid
import sys
from pathlib import Path
from types import SimpleNamespace as N
from decimal import Decimal
import pytest
from sqlalchemy import text,select,func,create_engine
from sqlalchemy.orm import sessionmaker
from engine.db import Session,engine
from engine.models import AnalysisDemand,UserPreference,StreamEvent,Outbox,Campaign,Episode
from engine.repository import emit,save_batch
from engine.transaction_events import add_outbox
from engine.p39 import pipeline
from engine.p37.detector import Detector,DEFAULTS
from engine.adapters.base import Batch
from engine.pd2.write_queue import Writer,encode,decode

IID='binance:perp:BTCUSDT'
def unique():return uuid.uuid4().hex

@pytest.fixture(autouse=True)
def independent_schema(monkeypatch):
    """Real independent connections; every test owns its entire scratch schema."""
    from engine.db import Base,BoundedSession
    from engine.repository import bootstrap
    original_engine=engine;original_session=Session
    assert original_engine.url.database.startswith('erji_p36_check_p39_')
    schema='deadlock_test_'+unique()
    with original_engine.begin() as c:c.execute(text('CREATE SCHEMA '+schema))
    isolated_engine=create_engine(original_engine.url,connect_args={'options':'-c search_path='+schema+' -c statement_timeout=10000'})
    factory=sessionmaker(bind=isolated_engine,class_=BoundedSession,expire_on_commit=False)
    for name,module in list(sys.modules.items()):
        if name.startswith(('engine.','services.')) or name==__name__:
            if getattr(module,'Session',None) is original_session:monkeypatch.setattr(module,'Session',factory)
            if getattr(module,'engine',None) is original_engine:monkeypatch.setattr(module,'engine',isolated_engine)
    try:
        Base.metadata.create_all(isolated_engine);bootstrap()
        b=Batch('binance')
        b.instruments=[dict(id=IID,asset_id='BTC',venue='binance',native_symbol='BTCUSDT',market_type='linear_perpetual',quote_asset='USDT',spec={'base_asset':'BTC','status':'TRADING'})]
        b.states=[dict(instrument_id=IID,as_of=1,received_at=1,price='100',price_kind='mark')]
        save_batch(b)
        yield
    finally:
        isolated_engine.dispose()
        with original_engine.begin() as c:c.execute(text('DROP SCHEMA '+schema+' CASCADE'))

def test_tail_publication_breaks_real_orm_lock_cycle():
    assert engine.url.database.startswith('erji_p36_check_p39_')
    key='tail:'+unique();held=threading.Event();queued=threading.Event();release=threading.Event();errors=[];receipts={}
    with Session.begin() as s:s.add(UserPreference(key=key,updated_at=0,payload={}))
    def a():
        try:
            with Session.begin() as s:
                s.execute(text('SET LOCAL statement_timeout=5000'))
                s.get(UserPreference,key,with_for_update=True);held.set();assert release.wait(4)
                receipts['a']=emit(s,'test_tail',{'key':key,'writer':'a'})
                # Old emit held the global lock while future business work ran.
                s.add(UserPreference(key=key+':after',updated_at=1,payload={}))
        except BaseException as e:errors.append(e)
    def b():
        try:
            assert held.wait(4)
            with Session.begin() as s:
                s.execute(text('SET LOCAL statement_timeout=5000'))
                row=s.get(UserPreference,key);row.payload={'writer':'b'}
                receipts['b']=emit(s,'test_tail',{'key':key,'writer':'b'})
                assert receipts['b'].id is None
                assert not s.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory' AND objid=1877101 AND granted)"))
                queued.set()
        except BaseException as e:errors.append(e)
    ta=threading.Thread(target=a);tb=threading.Thread(target=b);ta.start();tb.start()
    assert queued.wait(4);release.set();ta.join(7);tb.join(7)
    assert not ta.is_alive() and not tb.is_alive() and not errors,errors
    assert receipts['a'].id<receipts['b'].id
    with Session() as s:
        assert s.get(UserPreference,key).payload=={'writer':'b'}
        assert len(list(s.scalars(select(StreamEvent).where(StreamEvent.kind=='test_tail',StreamEvent.payload['key'].as_string()==key))))==2

def test_commit_guard_and_notification_preparation_precede_global_lock():
    from engine.p38.lifecycle import commit_guard
    key=unique();checked=[];receipt=None
    def guard(s):
        assert not s.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory' AND objid=1877101 AND granted)"))
        checked.append(True)
    token=commit_guard.set(guard)
    try:
        with Session.begin() as s:
            receipt=emit(s,'test_guard',{})
            for _ in range(2):add_outbox(s,Outbox(id=key,dedupe_key=key,state='pending',expires_at=1,next_attempt_at=1,payload={}),receipt)
    finally:commit_guard.reset(token)
    assert checked and receipt.id
    with Session() as s:assert s.get(Outbox,key).event_id==receipt.id

def test_failed_fence_publishes_neither_business_nor_event():
    from engine.p38.lifecycle import commit_guard
    key='fence:'+unique()
    def guard(s):raise RuntimeError('analysis_lease_lost')
    token=commit_guard.set(guard)
    try:
        with pytest.raises(RuntimeError,match='analysis_lease_lost'):
            with Session.begin() as s:
                s.add(UserPreference(key=key,updated_at=1,payload={}))
                emit(s,'test_fence',{'key':key})
    finally:commit_guard.reset(token)
    with Session() as s:
        assert s.get(UserPreference,key) is None
        assert s.scalar(select(func.count()).select_from(StreamEvent).where(StreamEvent.kind=='test_fence',StreamEvent.payload['key'].as_string()==key))==0

def test_rollback_then_reuse_session_does_not_leak_event_or_reservation():
    with Session() as s:
        emit(s,'test_rolled_back',{})
        s.begin();pipeline.lock_demands(s,['absent']);s.rollback()
        assert not s.info.get('p39_locked_demands') and not s.info.get('transaction_events')
        emit(s,'test_reused',{});s.commit()
    with Session() as s:assert not s.scalar(select(StreamEvent.id).where(StreamEvent.kind=='test_rolled_back'))

def test_reverse_input_lists_reserve_demands_in_common_order():
    sources=['sorted:'+unique() for _ in range(3)]
    with Session.begin() as s:
        pipeline.lock_demands(s,[pipeline.demand_id(IID,'candidate',src) for src in sources])
        for src in sources:pipeline.upsert(s,IID,'candidate',src,1,payload={'threshold':'100'})
    barrier=threading.Barrier(2);errors=[]
    def write(order):
        try:
            barrier.wait(3)
            with Session.begin() as s:
                s.execute(text('SET LOCAL statement_timeout=5000'))
                pipeline.lock_demands(s,[pipeline.demand_id(IID,'candidate',src) for src in order])
                for src in order:pipeline.upsert(s,IID,'candidate',src,2,payload={'threshold':'101'})
        except BaseException as e:errors.append(e)
    a=threading.Thread(target=write,args=(sources,));b=threading.Thread(target=write,args=(sources[::-1],));a.start();b.start();a.join(7);b.join(7)
    assert not a.is_alive() and not b.is_alive() and not errors,errors

def test_unchanged_candidates_take_no_demand_locks(monkeypatch):
    src=unique();at=int(time.time()*1000)
    with Session.begin() as s:pipeline.upsert(s,IID,'candidate',src,at,payload={'threshold':'100'})
    pipeline.wake_candidates({IID:(at,'101')},at)
    calls=[];original=pipeline.lock_demands
    def locked(s,ids):calls.append(list(ids));return original(s,ids)
    monkeypatch.setattr(pipeline,'lock_demands',locked)
    assert pipeline.wake_candidates({IID:(at+1,'102')},at+1)==0
    assert not calls
    assert pipeline.wake_candidates({IID:(at,'102')},at+16000)==0

def test_crossing_survives_failed_market_handoff(monkeypatch):
    src=unique();at=int(time.time()*1000)
    with Session.begin() as s:pipeline.upsert(s,IID,'candidate',src,at,payload={'threshold':'100'})
    original=pipeline.wake_markets
    def fail(*a,**k):raise RuntimeError('test_handoff_failure')
    monkeypatch.setattr(pipeline,'wake_markets',fail)
    with pytest.raises(RuntimeError,match='test_handoff_failure'):pipeline.wake_candidates({IID:(at,'101')},at)
    monkeypatch.setattr(pipeline,'wake_markets',original)
    assert pipeline.wake_candidates({IID:(at+1,'101')},at+1)==1
    with Session() as s:assert not s.get(AnalysisDemand,pipeline.demand_id(IID,'candidate',src)).payload['trigger_wake_pending']

def test_concurrent_real_selector_wake_result_and_candle_writers():
    at=int(time.time()*1000);campaign='concurrent:'+unique()
    with Session.begin() as s:
        s.add(Campaign(id=campaign,asset_id='BTC',reference_instrument_id=IID,first_seen_at=at,first_seen_price=100,state='active'));s.flush()
        for _ in range(4):s.add(Episode(id=unique(),campaign_id=campaign,strategy_id='test',phase='structure_pending',rule_version='test',evidence={'threshold':'100'}))
    pipeline.select_work(at,50,'structure',DEFAULTS)
    barrier=threading.Barrier(4);errors=[]
    def work(kind):
        try:
            barrier.wait(4)
            for n in range(8):
                tick=at+n*16000
                if kind=='select':pipeline.select_work(tick,50,'structure',DEFAULTS)
                elif kind=='wake':pipeline.wake_candidates({IID:(tick,'101' if n%2 else '99')},tick)
                elif kind=='result':
                    context=pipeline.begin_result(IID,'trend',unique(),{},tick)
                    pipeline.complete_result(context,{'strategy_result':'no_match'},tick+1)
                else:
                    ref=unique();b=Batch('binance',candle_only=True)
                    b.raw=[dict(id=ref,source='binance',endpoint='test',received_at=tick,payload={})]
                    b.candles=[dict(instrument_id=IID,resolution='60',open_time=0,close_time=3600000,is_closed=True,open='100',high='100',low='100',close='100',base_volume='1',quote_volume='100',received_at=tick,raw_ref=ref)]
                    save_batch(b)
        except BaseException as e:errors.append(e)
    with engine.connect() as c:before=c.scalar(text('SELECT deadlocks FROM pg_stat_database WHERE datname=current_database()'))
    threads=[threading.Thread(target=work,args=(k,)) for k in ('select','wake','result','candle')]
    for t in threads:t.start()
    for t in threads:t.join(20)
    assert all(not t.is_alive() for t in threads) and not errors,errors
    with engine.connect() as c:
        c.execute(text('SELECT pg_stat_clear_snapshot()'))
        after=c.scalar(text('SELECT deadlocks FROM pg_stat_database WHERE datname=current_database()'))
    assert after==before

class Deadlock(Exception):sqlstate='40P01'

def test_writer_retries_same_facts_without_early_ack():
    async def run():
        seen=[]
        async def write(item):
            seen.append(item.copy())
            if len(seen)==1:raise Deadlock()
        writer=Writer(write);writer.task=asyncio.create_task(writer.run())
        await writer.put({'at':123,'raw_ref':'original'});await writer.close()
        assert seen==[{'at':123,'raw_ref':'original'}]*2
        assert writer.committed==writer.accepted==1 and writer.retries==1 and writer.failed==0
    asyncio.run(run())

def test_permanent_failure_retains_inflight_and_queue_then_replays(tmp_path):
    async def run():
        gate=asyncio.Event();started=asyncio.Event()
        async def fail(item):started.set();await gate.wait();raise RuntimeError('database_permanent_failure')
        writer=Writer(fail,8,failure_path=tmp_path);writer.task=asyncio.create_task(writer.run())
        await writer.put({'at':1,'id':'one'});await started.wait();await writer.put({'at':2,'id':'two'});gate.set()
        with pytest.raises(RuntimeError):await writer.close()
        assert writer.committed==0 and writer.retained==2 and writer.queue._unfinished_tasks==2
        files=list(tmp_path.glob('*.json'));assert len(files)==1
        saved=[]
        async def good(item):saved.append(item)
        recovered=Writer(good,8,failure_path=tmp_path);recovered.task=asyncio.create_task(recovered.run())
        await recovered.put({'at':3,'id':'three'});await recovered.close()
        assert saved==[{'at':1,'id':'one'},{'at':2,'id':'two'},{'at':3,'id':'three'}]
        assert recovered.replayed==2 and not list(tmp_path.glob('*.json'))
    asyncio.run(run())

def test_retained_batch_preserves_native_times_and_decimal():
    b=Batch('binance',candle_only=True);b.states=[{'price':Decimal('1.23'),'as_of':123,'received_at':125,'raw_ref':'same'}]
    restored=decode(json.loads(json.dumps(encode(b))))
    assert isinstance(restored,Batch) and restored==b

def test_new_quote_is_not_overwritten_when_old_failed_batch_restored():
    from engine.pd2.native_streams import LatestQuotes
    q=LatestQuotes(capacity=2)
    old={'s':'BTCUSDT','E':100,'e':'markPriceUpdate'}
    q.offer(old,100);drained=q.drain()
    q.offer({**old,'E':200},200)
    q.restore(drained+[{'s':'ETHUSDT','E':100,'e':'markPriceUpdate'}])
    assert q.pending[('BTCUSDT','markPriceUpdate')]['E']==200
    assert q.pending[('ETHUSDT','markPriceUpdate')]['E']==100
    q.restore([{'s':'OTHERUSDT','E':100,'e':'markPriceUpdate'}])
    assert len(q.pending)==2 and q.rejected==1

def test_transaction_retry_uses_new_session_and_rolls_back_failed_event():
    from engine.db_retry import retry_transaction
    from engine.p38.lifecycle import commit_guard
    key='retry:'+unique();sessions=[];guards=[]
    def guard(s):
        guards.append(1)
        if len(guards)==1:raise Deadlock()
    @retry_transaction
    def write():
        with Session.begin() as s:
            sessions.append(s)
            s.add(UserPreference(key=key,updated_at=123,payload={'at':123}))
            receipt=emit(s,'test_retry',{'key':key,'at':123})
        return receipt
    token=commit_guard.set(guard)
    try:receipt=write()
    finally:commit_guard.reset(token)
    assert len(sessions)==2 and sessions[0] is not sessions[1] and receipt.id
    with Session() as s:
        assert s.get(UserPreference,key).updated_at==123
        assert s.scalar(select(func.count()).select_from(StreamEvent).where(StreamEvent.kind=='test_retry',StreamEvent.payload['key'].as_string()==key))==1

def test_unknown_database_failure_is_not_retried():
    from engine.db_retry import retry_transaction
    calls=[]
    @retry_transaction
    def unknown():calls.append(1);raise RuntimeError('unknown_commit_outcome')
    with pytest.raises(RuntimeError):unknown()
    assert calls==[1]

def test_exhausted_database_retry_retains_fact_without_ack(tmp_path):
    async def run():
        calls=[]
        async def fail(item):calls.append(item);raise Deadlock()
        writer=Writer(fail,failure_path=tmp_path);writer.task=asyncio.create_task(writer.run())
        await writer.put({'at':123,'raw_ref':'unchanged'})
        with pytest.raises(Deadlock):await writer.close()
        assert len(calls)==4 and writer.retries==3
        assert writer.retained==1 and writer.committed==0 and writer.queue._unfinished_tasks==1
    asyncio.run(run())

def test_contract_detector_keeps_mark_basis_through_trade_fallback():
    d=Detector();d.catalog[IID]={'venue':'binance'};at=1700000000000
    for n in range(301):d.price(IID,at+n*1000,'100','mark')
    d.price(IID,at+316000,'100','last')
    assert len(d.prices[IID])==301 and d.price_kinds[IID]=='mark'
    d.price(IID,at+317000,'101','mark')
    assert len(d.prices[IID])==302
    assert d.delta(d.prices[IID],at+317000,300000) is None

def test_db_maintenance_failure_does_not_stop_independent_receive():
    from engine.pd2.stream_maintenance import maintain
    async def run():
        calls=[];frames=[]
        async def fn():
            calls.append(1)
            if len(calls)==1:raise Deadlock()
            await asyncio.sleep(.01)
        task=asyncio.create_task(maintain(fn,.001))
        try:
            for n in range(12):frames.append(n);await asyncio.sleep(.002)
            assert len(frames)==12 and len(calls)>1 and not task.done()
        finally:task.cancel();await asyncio.gather(task,return_exceptions=True)
    asyncio.run(run())

def test_only_failed_native_shard_is_restarted(monkeypatch):
    import engine.repository as repository
    from engine.pd2 import native_streams as streams
    async def run():
        calls={};ready=asyncio.Event();original_sleep=asyncio.sleep
        async def sleep(seconds):await original_sleep(.001 if seconds==60 else seconds)
        async def stream(venue,**spec):
            key=(spec.get('channel'),spec.get('shard'));calls[key]=calls.get(key,0)+1
            if key==('market',0) and calls[key]==1:raise RuntimeError('one_shard')
            if calls.get(('market',0),0)==2:ready.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(repository,'runtime_config',lambda:N(collector_enabled=True))
        monkeypatch.setattr(streams,'subscriptions',lambda _:list(range(129)))
        monkeypatch.setattr(streams,'stream_venue',stream);monkeypatch.setattr(streams.asyncio,'sleep',sleep)
        task=asyncio.create_task(streams.venue_stream_group('binance'))
        try:
            await asyncio.wait_for(ready.wait(),2)
            assert calls[('market',0)]==2
            assert all(v==1 for k,v in calls.items() if k!=('market',0))
        finally:task.cancel();await asyncio.gather(task,return_exceptions=True)
    asyncio.run(run())
