"""Regression for post-start queue starvation, bounded reads and discovery."""
import asyncio,time,threading
from sqlalchemy import select
from engine.models import Task,SignalSnapshot,Plan,UserPreference,MarketState
from engine.p38 import jobs,windows
from engine.p38.plan_queries import live_plans,latest_ids
from engine.p37 import detector as discovery
from engine.pd2 import native_streams,scheduling
from test_p25_signals import isolated
from test_p39_reliability import seed_plan

def test_global_mark_packets_build_continuous_contract_windows(monkeypatch):
    d=discovery.Detector();iid='binance:perp:ABCUSDT';d.catalog={iid:{}}
    monkeypatch.setattr(discovery,'detector',d);at=2000000000000
    for n in range(901):
        packet={'data':[{'e':'markPriceUpdate','s':'ABCUSDT','E':at+n*1000,'p':'106' if n==900 else '100'}]}
        discovery.ingest('binance',packet,at+n*1000+20)
    f=d.facts(iid,at+900000)
    assert f['changes']=={'5':6.0,'15':6.0} and f['basis_price']=='106'
    before=list(d.prices[iid])
    discovery.ingest('binance',{'data':{'e':'markPriceUpdate','s':'ABCUSDT','E':at+920000,'p':'200'}},at+940000)
    assert list(d.prices[iid])==before
    discovery.ingest('binance',{'data':{'e':'markPriceUpdate','s':'UNKNOWN','E':at+901000,'p':'200'}},at+901000)
    assert len(d.prices)==1

def test_price_source_switch_cannot_create_false_continuity():
    d=discovery.Detector();d.catalog={'x':{}}
    for n in range(301):d.price('x',1000000+n*1000,'100','mark')
    d.price('x',1301000,'120','last')
    assert d.prices['x'][-1]==(1300000,'100')
    d.price('x',1320000,'120','last')
    assert len(d.prices['x'])==1 and d.delta(d.prices['x'],1320000,300000) is None
    d.price('x',1321000,'100','mark')
    assert len(d.prices['x'])==1

def test_quote_admission_and_scope_refresh_preserve_original_times():
    q=native_streams.LatestQuotes(symbols={'A','B'})
    q.offer({'data':[{'e':'markPriceUpdate','s':s,'E':100,'p':'1'} for s in ('A','B','UNLISTED')]},110)
    assert len(q.pending)==2 and q.filtered==1
    q.restrict({'A'});assert len(q.pending)==1 and q.filtered==2
    row=q.drain()[0];assert row['E']==100 and row['_received_at']==110

def test_window_batch_matches_bounded_single_tokens(isolated):
    at=int(time.time()*1000)
    with isolated() as s:
        batch=windows.batch_tokens(s,['binance:perp:BTCUSDT','missing'],('60','240'),at,{'60':7})
        assert dict(batch['binance:perp:BTCUSDT'])['60']==windows.token(s,'binance:perp:BTCUSDT','60',at,limit=7)
        assert dict(batch['binance:perp:BTCUSDT'])['60'][0]==7
        assert dict(batch['missing'])['60']==(0,None,None,None)

def test_expired_entry_retires_but_protection_and_history_remain(isolated):
    at=int(time.time()*1000)
    with isolated.begin() as s:iid,old=seed_plan(s,at,expired=True)
    targets=jobs.managed_targets(at)
    assert iid not in targets['entry'] and iid in targets['protect'] and iid in targets['projection']
    jobs.submit([iid],at=at,work_kind='entry')
    jobs.retire_unneeded(targets)
    with isolated() as s:
        assert s.get(Task,jobs.identity(iid,'entry')).payload['retired_reason']=='no_live_entry'
        assert s.get(SignalSnapshot,'p39-old').payload==old and s.get(Plan,'p39-p')
    assert jobs.take(at,work_kinds=['entry']) is None

def test_ended_latest_plan_is_not_managed(isolated):
    at=int(time.time()*1000)
    with isolated.begin() as s:
        iid,_=seed_plan(s,at)
        s.add(SignalSnapshot(id='new-ended',plan_id='p39-p',as_of=at,payload={'action':{'code':'ended'}}))
    with isolated() as s:
        assert not list(s.execute(live_plans(at)))
        assert s.scalar(latest_ids([iid]))=='new-ended'
        assert s.scalar(latest_ids(['other'])) is None

def test_deep_retirement_can_readmit_same_inputs(isolated):
    at=int(time.time()*1000);jobs.submit(['x'],at=at,work_kind='trend')
    jobs.retire_unneeded({'entry':[],'deep':[]})
    jobs.submit(['x'],at=at,work_kind='trend')
    assert jobs.take(at,work_kinds=['trend'])['instrument_id']=='x'

def test_fast_lane_not_blocked_by_old_deep_backlog(isolated):
    at=int(time.time()*1000)
    jobs.submit(['deep'],at=at-600000,work_kind='trend')
    jobs.submit(['fast'],at=at,work_kind='entry')
    assert jobs.take(at)['work_kind']=='entry'
    assert jobs.take(at,work_kinds=jobs.LANES['trend'],exclude_subjects=['fast'])['instrument_id']=='deep'

def test_dispatch_continues_while_producer_is_blocked(monkeypatch):
    from engine.pd2 import execution
    from engine import repository
    assert jobs.engine.url.database.startswith('erji_p36_check_')
    started=threading.Event();release=threading.Event();claims=[];running=[]
    class Adapter:
        async def close(self):pass
    def submit(*args,**kw):
        started.set();assert release.wait(5)
    def take(*args,work_kinds=None,**kw):
        lane=tuple(work_kinds)
        if lane in claims:return None
        claims.append(lane);kind=work_kinds[0]
        return dict(instrument_id=kind,work_kind=kind)
    async def execute(lease,adapter):
        running.append(lease['work_kind']);await asyncio.Event().wait()
    monkeypatch.setattr(execution,'ExecutionAdapter',Adapter)
    monkeypatch.setattr(jobs,'managed_targets',lambda:{'entry':[],'protect':[],'projection':[],'deep':[]})
    monkeypatch.setattr(jobs,'retire_unneeded',lambda _:None)
    monkeypatch.setattr(jobs,'submit',submit);monkeypatch.setattr(jobs,'take',take);monkeypatch.setattr(jobs,'execute',execute)
    monkeypatch.setattr(repository,'heartbeat',lambda *args:None)
    async def scenario():
        task=asyncio.create_task(jobs.run())
        try:
            assert await asyncio.to_thread(started.wait,2)
            deadline=time.monotonic()+2
            while len(running)<5 and time.monotonic()<deadline:await asyncio.sleep(.01)
            assert set(running)=={'protect','entry','projection','trend','structure'}
        finally:
            release.set();task.cancel()
            await asyncio.gather(task,return_exceptions=True)
    asyncio.run(scenario())

def test_projection_new_quote_coalesces_instead_of_cancelling(monkeypatch,isolated):
    from engine.pd2 import service
    at=int(time.time()*1000)
    with isolated.begin() as s:iid,_=seed_plan(s,at)
    jobs.submit([iid],at=at,work_kind='projection');lease=jobs.take(at,work_kinds=['projection'])
    def project(**kwargs):
        with isolated.begin() as s:s.get(MarketState,iid).as_of=at+1
        jobs.submit([iid],at=at+2,work_kind='projection')
        return {}
    monkeypatch.setattr(service,'decision_list',project)
    asyncio.run(jobs.execute(lease,None))
    with isolated() as s:
        row=s.get(Task,lease['id'])
        assert row.state=='complete' and not row.payload['error'] and row.payload['completed_count']==1

def test_source_revision_still_rejects_projection_commit(monkeypatch,isolated):
    from engine.pd2 import service
    at=int(time.time()*1000)
    with isolated.begin() as s:iid,_=seed_plan(s,at)
    jobs.submit([iid],at=at,work_kind='projection');lease=jobs.take(at,work_kinds=['projection'])
    def project(**kwargs):
        with isolated.begin() as s:s.merge(UserPreference(key='p37_config',updated_at=at+1,payload={}))
        return {}
    monkeypatch.setattr(service,'decision_list',project)
    asyncio.run(jobs.execute(lease,None))
    with isolated() as s:
        row=s.get(Task,lease['id']);assert row.state=='queued' and row.payload['deferred_reason']=='analysis_source_changed'
        assert row.payload.get('failed_count',0)==0

def test_scheduling_fields_skip_large_evidence(isolated,monkeypatch):
    from engine.pd2 import p35_schedule
    from types import SimpleNamespace
    from engine.models import Episode
    from sqlalchemy import event
    at=int(time.time()*1000)
    with isolated.begin() as s:
        iid,_=seed_plan(s,at)
        s.get(Episode,'p39-e').evidence={'large_irrelevant':'x'*1000000}
        s.add(UserPreference(key='p35_background:'+iid,updated_at=at,payload={'needs_trigger_data':True,'priority_until':at+1000}))
    monkeypatch.setattr(p35_schedule,'now_ms',lambda:at);statements=[]
    with isolated() as s:
        def capture(conn,cursor,statement,*args):statements.append(statement)
        event.listen(s.bind,'before_cursor_execute',capture)
        try:
            assert iid in p35_schedule.hourly_dependencies(s,SimpleNamespace(disabled_strategies=[]))
            assert iid in p35_schedule.priority_instruments(s)
            assert p35_schedule.execution_assets(s,at,asset_ids=['P39TEST'])=={'P39TEST'}
        finally:event.remove(s.bind,'before_cursor_execute',capture)
    assert not any('episodes.evidence,' in query or 'episodes.evidence \n' in query for query in statements)

def test_snapshot_index_migration_in_disposable_cluster():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from migrations.versions import p39_snapshot_latest
    from sqlalchemy import text
    assert jobs.engine.url.database.startswith('erji_p36_check_')
    with jobs.engine.connect() as connection:
        context=MigrationContext.configure(connection,opts={'transaction_per_migration':True})
        with context.begin_transaction(_per_migration=True):
            with Operations.context(context):
                p39_snapshot_latest.downgrade()
                p39_snapshot_latest.upgrade()
                p39_snapshot_latest.upgrade() # Safe to rerun an existing index.
        definition=connection.execute(text("SELECT indexdef FROM pg_indexes WHERE indexname='ix_signal_snapshots_plan_latest'")).scalar()
        assert '(plan_id, as_of DESC)' in definition
