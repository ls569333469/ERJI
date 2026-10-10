import asyncio
import threading
from types import SimpleNamespace
import pytest
from engine.pd2.candle_window import CandleWindow
from engine.pd2.continuous_collection import dispatch
from engine.pd2.write_queue import writing
from engine.pd2 import p36_budget as budget
from test_p25_signals import isolated


def bar(at,closed=True,received=100):
    return dict(open_time=at,close_time=at+10,is_closed=closed,received_at=received,close='1.00000001')


def test_window_prefix_gap_and_closed_precedence():
    w=CandleWindow(4);w.merge([bar(20),bar(30)])
    assert w.missing(40,10,4)==0
    w.merge([bar(0),bar(10),bar(20,False,200)])
    assert w.ready(40,10,4) and w.rows[20]['is_closed']
    w.merge([bar(40)])
    assert set(w.rows)=={10,20,30,40}
    assert w.rows[40]['close']=='1.00000001'


def test_unclosed_and_internal_gap_are_not_ready():
    w=CandleWindow(3);w.merge([bar(0),bar(20,False)])
    assert w.missing(30,10,3)==10
    w.merge([bar(10)])
    assert w.missing(30,10,3)==20
    assert not w.ready(30,10,3)


def test_new_listing_never_invents_old_bars():
    w=CandleWindow(3);w.merge([bar(20)])
    assert w.missing(30,10,3,onboard=20) is None
    assert not w.ready(30,10,3)


def test_dispatch_refills_without_slow_peer():
    async def run():
        slow=asyncio.Event();fast=asyncio.Event();seen=[];keys=['slow','fast','next']
        async def choose(n):result=keys[:n];del keys[:n];return result
        async def execute(key):
            seen.append(key)
            if key=='slow':await slow.wait()
            if key=='next':fast.set()
        async def enabled():return not fast.is_set()
        await asyncio.wait_for(dispatch(choose,execute,enabled,2),2)
        assert seen==['slow','fast','next'] and not slow.is_set()
    asyncio.run(run())


def test_writer_copies_facts_and_drains_before_exit():
    async def run():
        saved=[]
        async def write(item):await asyncio.sleep(.001);saved.append(item)
        async with writing(write,2) as writer:
            value={'price':'1'};await writer.put(value);value['price']='2'
        assert saved==[{'price':'1'}]
    asyncio.run(run())


def test_writer_failure_cannot_be_acknowledged():
    async def run():
        async def write(item):raise RuntimeError('db_unavailable')
        with pytest.raises(RuntimeError,match='db_unavailable'):
            async with writing(write,1) as writer:await writer.put({'id':1})
    asyncio.run(run())


def test_owner_waiting_has_no_db_work(monkeypatch):
    state={'entries':[],'pending':[],'leases':{},'buckets':{},'served':{},'events':[]}
    monkeypatch.setattr(budget,'_STORE',SimpleNamespace(lock=threading.RLock(),payload=state))
    monkeypatch.setattr(budget,'now_ms',lambda:2000000000000)
    class NoDatabase:
        def begin(self):raise AssertionError('database called while waiting')
    monkeypatch.setattr(budget,'Session',NoDatabase())
    url='https://fapi.binance.com/fapi/v1/openInterest'
    assert budget.admit(url,{},'first','live')==0
    budget.sent('first')
    for _ in range(10):assert budget.admit(url,{},'next','repair')>0
    assert len(budget._STORE.payload['entries'])==1
    budget.feedback(url,429,{'retry-after':'120'}, {},'first')
    assert budget.admit(url,{},'next','repair')>=120
    budget.release('first')
    assert budget._STORE.payload['entries'][0]['sent']


def test_oi_schedule_uses_faster_policy_without_changing_other_sources(isolated,monkeypatch):
    from engine.pd2 import collection_schedule as schedule
    from engine.models import CurrentSignal
    from test_p25_signals import seed,NOW
    seed(isolated,3,action='waiting')
    with isolated.begin() as session:
        ordinary=session.get(CurrentSignal,'p25plan0');session.delete(ordinary)
        active=session.get(CurrentSignal,'p25plan2');active.action='participate'
    monkeypatch.setattr(schedule,'now_ms',lambda:NOW)
    names=['P25TEST0USDT','P25TEST1USDT','P25TEST2USDT']
    schedule.acknowledge('binance',names,at=NOW-14000)
    assert schedule.due('binance',names)==['P25TEST2USDT']
    schedule.acknowledge('binance',['P25TEST2USDT'],at=NOW)
    monkeypatch.setattr(schedule,'now_ms',lambda:NOW+1000)
    assert schedule.due('binance',names)==['P25TEST1USDT']
    schedule.acknowledge('binance',['P25TEST1USDT'],at=NOW+1000)
    monkeypatch.setattr(schedule,'now_ms',lambda:NOW+46000)
    assert 'P25TEST0USDT' in schedule.due('binance',names)


def test_local_owner_rpc_and_durable_ban(tmp_path,monkeypatch):
    import os,socket,subprocess,sys,time
    from sqlalchemy.engine import make_url
    from engine.pd2 import budget_owner
    from engine.models import UserPreference
    from engine.db import Session
    assert make_url(os.environ['ERJI_DATABASE_URL']).database.startswith('erji_p36_check_')
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    token=tmp_path/'owner.token';env=dict(os.environ,ERJI_BUDGET_PORT=str(port),ERJI_BUDGET_TOKEN_FILE=str(token))
    proc=subprocess.Popen([sys.executable,'-m','engine.pd2.budget_owner'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    monkeypatch.setattr(budget_owner,'PORT',port);monkeypatch.setattr(budget_owner,'TOKEN',token)
    try:
        for _ in range(300):
            if token.exists():break
            if proc.poll() is not None:pytest.fail('owner startup failed')
            time.sleep(.02)
        url='https://fapi.binance.com/fapi/v1/openInterest'
        assert budget_owner.call('admit',(url,{},'rpc','live'))>0  # recovery window
        budget_owner.call('feedback',(url,429,{'retry-after':'600'},{},None))
        with Session() as session:assert session.get(UserPreference,budget.KEY).payload['binance_ban_until']>budget.now_ms()+500000
        assert budget_owner.call('admit',(url,{},'rpc','live'))>500
        budget_owner.call('release',('rpc',))
    finally:
        proc.terminate();proc.wait(timeout=10)
        with Session.begin() as session:
            row=session.get(UserPreference,budget.KEY)
            if row:session.delete(row)
