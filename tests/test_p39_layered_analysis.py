"""Causal boundaries and replay in a disposable PostgreSQL, no exchange I/O."""
import time
from copy import deepcopy
from decimal import Decimal as D
from sqlalchemy import select
from engine.p39 import pipeline,diagnostics
from engine import signal_service
from engine.p37 import worker,store,analysis
from engine.p38 import jobs
from engine.models import (Asset,Instrument,Observation,AnalysisDemand,AnalysisResult,
    MarketAnalysisState,UserPreference,Episode,Campaign,Task)
from services.api import main as api_main, observations as api_observations
from engine.p37.detector import Detector,DEFAULTS
from test_p25_signals import isolated

def seed(s,iid,at,status='observing',age=0):
    asset=iid.rsplit(':',1)[-1]
    s.add(Asset(id=asset,symbol=asset,known_at=at));s.flush()
    s.add(Instrument(id=iid,asset_id=asset,venue='binance',native_symbol=asset+'USDT',market_type='linear_perpetual',quote_asset='USDT',spec={}));s.flush()
    obs=Observation(id='o:'+iid,instrument_id=iid,asset_id=asset,direction='up',first_at=at-age,
        last_at=at-age,visible_at=at-age,status=status,version=at,payload={'symbol':asset,'visible_price':'10'})
    s.add(obs);s.flush();return obs

def test_cooling_and_dormant_candidate_do_not_admit_until_crossing(isolated):
    at=int(time.time()*1000)
    with isolated.begin() as s:
        old=seed(s,'test:old',at,'cooling',DEFAULTS['cool_ms']+1)
        new=seed(s,'test:new',at)
    chosen,_=pipeline.select_work(at,50,'structure',DEFAULTS)
    assert {iid for _,iid in chosen}=={'test:new'}
    with isolated.begin() as s:
        s.add(Campaign(id='c',asset_id=old.asset_id,reference_instrument_id=old.instrument_id,first_seen_at=at,first_seen_price=D(10),state='active'));s.flush()
        s.add(Episode(id='candidate',campaign_id='c',strategy_id='arc',phase='structure_pending',rule_version='test',evidence={'structure_valid_until':at-100,'threshold':'12'}))
        pipeline.synchronize(s,at+1,DEFAULTS)
    assert {iid for _,iid in pipeline.select_work(at+2,50,'structure',DEFAULTS)[0]}=={'test:new'}
    assert pipeline.wake_candidates({old.instrument_id:(at+2,'12.1')},at+2)==1
    assert {iid for _,iid in pipeline.select_work(at+2,50,'structure',DEFAULTS)[0]}=={'test:old','test:new'}
    with isolated.begin() as s:s.get(Episode,'candidate').phase='ended';pipeline.synchronize(s,at+3,DEFAULTS)
    assert {iid for _,iid in pipeline.select_work(at+4,50,'structure',DEFAULTS)[0]}=={'test:new'}
    with isolated() as s:assert s.get(Observation,old.id).status=='cooling'


def test_epoch_millisecond_source_shadow_and_capacity_queries(isolated):
    at=1791389661776
    with isolated.begin() as s:
        obs=seed(s,'test:millisecond',at)
        obs.payload={**obs.payload,'persisted_at':at}
        pipeline.synchronize(s,at,DEFAULTS)
    context=pipeline.begin_result(obs.instrument_id,'structure','epoch-test',{},at)
    pipeline.complete_result(context,{'strategy_result':'no_match'},at+250)
    metrics=pipeline.capacity_metrics(at+251)
    assert metrics['independent_market_input_completions']['structure']==1
    assert metrics['service_ms']['structure']['mean']==250
    with isolated() as s:
        demand=s.scalar(select(AnalysisDemand).where(AnalysisDemand.instrument_id==obs.instrument_id))
        assert demand.payload['source']['persisted_at']==at
    pipeline.baseline_tick(at)
    with isolated.begin() as s:
        shadow=s.scalar(select(AnalysisDemand).where(AnalysisDemand.state=='shadow'))
        assert shadow is not None
        shadow.payload={**shadow.payload,'checked_at':at}
    # A previously checked epoch timestamp must be representable even when
    # filtered out of the next shadow submission.
    assert pipeline.submit_shadow(at+1)==0

def test_multiple_sources_keep_exact_market_and_result_provenance(isolated):
    at=int(time.time()*1000)
    with isolated.begin() as s:
        a=seed(s,'test:a',at);b=seed(s,'test:b',at)
        pipeline.upsert(s,a.instrument_id,'observation',a.id,at,at+10000,{'source_at':at})
        pipeline.upsert(s,a.instrument_id,'manual',a.id,at,payload={'observation_id':a.id})
        pipeline.upsert(s,b.instrument_id,'observation',b.id,at,at+10000,{'source_at':at})
    context=pipeline.begin_result('test:a','structure','version',{'60':{'revision':1}},at)
    origin=pipeline.origin(context)
    assert origin['origin_observation_ids']==[a.id] and len(origin['demand_ids'])==2
    assert origin['analysis_market_id']=='test:a' and b.id not in str(origin)
    pipeline.complete_result(context,{'strategy_result':'no_match'},at+1)
    with isolated() as s:
        result=s.get(AnalysisResult,context['result_id']);state=s.get(MarketAnalysisState,('test:a','structure'))
        assert result.payload['status']=='completed' and state.strategy_result=='no_match'
        assert state.result_id==result.id and state.last_success_at==at+1
    trace=diagnostics.trace('test:a')
    assert trace['results'][0]['id']==context['result_id'] and trace['demands']
    assert pipeline.origin()['origin_type']=='legacy_unlinked'

def test_missing_inputs_releases_slot_and_new_source_can_wake(isolated):
    at=int(time.time()*1000)
    with isolated.begin() as s:a=seed(s,'test:wait',at)
    pipeline.select_work(at,1,'structure',DEFAULTS)
    context=pipeline.begin_result(a.instrument_id,'structure','v1',{},at)
    pipeline.complete_result(context,{'waiting_inputs':True},at+1)
    assert pipeline.select_work(at+2,1,'structure',DEFAULTS)[0]==[]
    with isolated.begin() as s:
        r=s.get(Observation,a.id);r.last_at=at+3
        pipeline.upsert(s,a.instrument_id,'observation',a.id,at+3,at+3+DEFAULTS['cool_ms'],{'source_at':at+3})
    assert pipeline.select_work(at+4,1,'structure',DEFAULTS)[0][0][1]==a.instrument_id

def test_clock_does_not_change_compute_version_or_cancel_lease(isolated):
    at=int(time.time()*1000);iid='binance:perp:BTCUSDT'
    v=jobs.dependency_versions([iid],at,'structure')[iid]
    assert jobs.dependency_versions([iid],at+60000,'structure')[iid]==v
    jobs.submit([iid],at=at);lease=jobs.take(at,work_kinds=['structure'])
    jobs.submit([iid],at=at+60000)
    with isolated() as s:assert s.get(Task,lease['id']).payload['desired_version']==lease['version']
    assert jobs.finish(lease,at=at+60001)
    jobs.submit([iid],at=at+60002)
    assert jobs.take(at+60002,work_kinds=['structure']) is None
    jobs.submit([iid],at=at+360002)
    assert jobs.take(at+360002,work_kinds=['structure']) is None
    jobs.submit([iid],at=at+660002)
    assert jobs.take(at+660002,work_kinds=['structure'])['version']==lease['version']

def test_manual_unpin_and_shadow_rotation_do_not_invent_events(isolated):
    at=int(time.time()*1000)
    with isolated.begin() as s:a=seed(s,'test:manual',at,'cooling',DEFAULTS['cool_ms']+1)
    store.pin(a.id,True)
    assert pipeline.select_work(at+2,1,'structure',DEFAULTS)[0]
    store.pin(a.id,False)
    assert pipeline.select_work(at+3,1,'structure',DEFAULTS)[0]==[]
    before=None
    with isolated() as s:before=s.scalar(select(Observation.id).where(Observation.instrument_id=='binance:perp:BTCUSDT'))
    assert pipeline.baseline_tick(at,batch_size=1)
    with isolated() as s:
        assert s.scalar(select(AnalysisDemand).where(AnalysisDemand.origin_type=='baseline_screen')).state=='shadow'
        assert s.scalar(select(Observation.id).where(Observation.instrument_id=='binance:perp:BTCUSDT'))==before

def test_metric_direction_and_windows_are_unambiguous():
    now=300000*100;d=Detector();iid='binance:perp:AAAUSDT'
    d.catalog={iid:{'oi_unit':'AAA','quote_asset':'USDT'}}
    for at in range(now-900000,now+1,1000):d.price(iid,at,'104' if at==now else '100','mark')
    d.oi_point(iid,now-900000,'100');d.oi_point(iid,now,'110')
    for n in range(11):d.bar(iid,{'open_time':now-n*300000,'open':'100','close':'100','volume':'400000' if n==0 else '100000','closed':n!=0,'at':now})
    reasons={r['kind']:r for r in d.facts(iid,now)['reasons']}
    assert reasons['price']['window_kind']=='rolling' and reasons['price']['price_kind']=='mark'
    assert reasons['oi']['metric_direction']=='up' and reasons['oi']['price_direction'] is None and reasons['oi']['quantity_unit']=='AAA'
    assert reasons['volume']['intrabar'] and not reasons['volume']['bar_closed'] and reasons['volume']['baseline_count']==10

def test_volume_field_clock_does_not_follow_mark_or_oi():
    from engine.repository import merge_market_payload
    old={'as_of':200,'price':'10','volume_24h_quote':'100','volume_24h_source_at':100,'volume_24h_received_at':105}
    merged=merge_market_payload(old,{'as_of':300,'price':'11','received_at':301})
    assert merged['volume_24h_source_at']==100
    merged=merge_market_payload(merged,{'as_of':400,'price':'12','volume_24h_quote':'99','volume_24h_source_at':99,'received_at':401})
    assert merged['volume_24h_quote']=='100' and merged['price']=='12'
    current=merge_market_payload({'quote_volume_24h':'80','volume_24h_source_at':100},
        {'volume_24h_quote':'110','as_of':200,'received_at':201})
    assert current['quote_volume_24h']==current['volume_24h_quote']=='110'

def test_input_backfill_wakes_retry_without_creating_unrequested_work(isolated):
    at=int(time.time()*1000)
    with isolated.begin() as s:a=seed(s,'test:input-wake',at)
    pipeline.select_work(at,1,'structure',DEFAULTS)
    jobs.submit([a.instrument_id],at=at)
    lease=jobs.take(at,work_kinds=['structure'])
    context=pipeline.begin_result(a.instrument_id,'structure',lease['version'],{},at)
    pipeline.complete_result(context,{'waiting_inputs':True},at+1)
    jobs.finish(lease,at=at+2)
    with isolated() as s:assert s.get(Task,lease['id']).payload['next_review_at']==at+120001
    assert pipeline.select_work(at+3,1,'structure',DEFAULTS)[0]==[]
    assert pipeline.wake_markets([a.instrument_id,'ordinary'],at+4,'closed_candle_or_revision')==1
    assert pipeline.select_work(at+5,1,'structure',DEFAULTS)[0]
    jobs.submit([a.instrument_id],at=at+6)
    assert jobs.take(at+6,work_kinds=['structure'])['version']==lease['version']
    with isolated() as s:assert not pipeline.has_demand(s,'ordinary',at+6)

def test_candidate_price_wake_is_native_and_does_not_publish(isolated):
    at=int(time.time()*1000)
    with isolated.begin() as s:
        a=seed(s,'test:candidate-wake',at,'cooling',DEFAULTS['cool_ms']+1)
        pipeline.upsert(s,a.instrument_id,'candidate','real-candidate',at,payload={'threshold':'12'})
    assert pipeline.wake_candidates({a.instrument_id:(at,'11')},at)==0
    assert pipeline.wake_candidates({a.instrument_id:(at,'12.1')},at+1)==1
    assert pipeline.wake_candidates({a.instrument_id:(at,'12.2')},at+2)==0
    assert pipeline.wake_candidates({'different-market':(at,'13')},at+3)==0
    assert pipeline.wake_candidates({a.instrument_id:(at,'11')},at+16000)==0

def test_same_asset_markets_keep_independent_demand_and_inputs(isolated):
    at=int(time.time()*1000)
    with isolated.begin() as s:
        a=seed(s,'test:native-a',at)
        s.add(Instrument(id='test:native-b',asset_id=a.asset_id,venue='binance_spot',native_symbol='NATIVEUSDT',
            market_type='spot',quote_asset='USDT',spec={}));s.flush()
        s.add(Observation(id='explicit-spot-watch',instrument_id='test:native-b',asset_id=a.asset_id,direction='up',
            first_at=at,last_at=at,visible_at=at,status='observing',version=at,payload={'manual':True,'pinned':True}))
    chosen=pipeline.select_work(at,50,'structure',DEFAULTS)[0]
    assert {iid for _,iid in chosen}=={a.instrument_id,'test:native-b'}
    one=pipeline.begin_result(a.instrument_id,'structure','perp-input',{},at)
    two=pipeline.begin_result('test:native-b','structure','spot-input',{},at)
    assert pipeline.origin(one)['analysis_market_id']!=pipeline.origin(two)['analysis_market_id']
    assert pipeline.origin(one)['origin_observation_ids']==[a.id]
    assert pipeline.origin(two)['origin_observation_ids']==[]

def test_source_upsert_cannot_regress_and_shadow_rotation_revisits(isolated):
    at=int(time.time()*1000);iid='binance:perp:BTCUSDT'
    with isolated.begin() as s:
        d=pipeline.upsert(s,iid,'manual','watch',at,payload={'source_at':at})
        ident=d.id
        pipeline.upsert(s,iid,'manual','watch',at-1,payload={'source_at':at-1},state='ended')
    with isolated() as s:assert s.get(AnalysisDemand,ident).state=='active'
    pipeline.baseline_tick(at)
    with isolated.begin() as s:
        d=s.scalar(select(AnalysisDemand).where(AnalysisDemand.origin_type=='baseline_screen'))
        d.payload={**d.payload,'checked_at':at};did=d.id
    pipeline.baseline_tick(at+60000)
    with isolated() as s:assert 'checked_at' in s.get(AnalysisDemand,did).payload
    pipeline.baseline_tick(at+300000)
    with isolated() as s:assert 'checked_at' not in s.get(AnalysisDemand,did).payload

def test_volume_qualification_uses_its_own_clock(isolated):
    from engine.models import MarketState
    at=int(time.time()*1000);iid='binance:perp:BTCUSDT'
    with isolated.begin() as s:
        s.get(MarketState,iid).payload={'price':'100','as_of':at,'quote_volume_24h':'2000000',
            'volume_24h_source_at':at-1000000,'volume_24h_received_at':at}
    meta,_=store.catalog()
    assert meta[iid]['liquidity'] is None and meta[iid]['liquidity_status']=='stale'
    with isolated.begin() as s:
        row=s.get(MarketState,iid);row.payload={**row.payload,'volume_24h_source_at':at}
    assert store.catalog()[0][iid]['liquidity']==2000000

def test_actual_structure_owner_preserves_sources_and_rejects_midstage_revision(isolated,monkeypatch):
    import asyncio
    from engine.models import Candle
    from engine.pd2 import service
    from engine import signal_service
    at=int(time.time()*1000);iid='binance:perp:BTCUSDT'
    with isolated.begin() as s:
        s.add(Observation(id='actual-owner-source',instrument_id=iid,asset_id='BTC',direction='up',
            first_at=at,last_at=at,visible_at=at,status='observing',version=at,payload={}))
    pipeline.select_work(at,50,'structure',DEFAULTS)
    jobs.submit([iid],at=at);lease=jobs.take(at,work_kinds=['structure'])
    class NoNetwork:
        async def collect(self,*args):raise AssertionError('T2 must not collect books')
    seen=[]
    def scan(ids,**kwargs):seen.append(pipeline.origin());return {'scanned':1,'matched':0}
    monkeypatch.setattr(signal_service,'scan_signals',scan)
    monkeypatch.setattr(service,'refresh_plans',lambda *args,**kw:{'snapshots':0})
    monkeypatch.setattr(service,'decision_list',lambda **kw:None)
    asyncio.run(jobs.execute(lease,NoNetwork()))
    with isolated() as s:
        state=s.get(MarketAnalysisState,(iid,'structure'))
        assert state.analysis_status=='completed' and state.strategy_result=='no_match'
        assert seen[0]['analysis_result_id']==state.result_id
        assert seen[0]['origin_observation_ids']==['actual-owner-source']
    # A revision between stages must not be relabelled as the first manifest.
    def revise(ids,**kwargs):
        seen.append(pipeline.origin())
        with isolated.begin() as s:
            row=s.scalar(select(Candle).where(Candle.instrument_id==iid,Candle.resolution=='60'))
            row.revision+=1
        return {'scanned':1,'matched':0}
    monkeypatch.setattr(signal_service,'scan_signals',revise)
    pipeline.wake_markets([iid],at+10,'candidate_price_crossing')
    jobs.submit([iid],at=at+11);second=jobs.take(at+11,work_kinds=['structure'])
    asyncio.run(jobs.execute(second,NoNetwork()))
    with isolated() as s:
        task=s.get(Task,second['id'])
        assert task.state=='queued' and task.payload['deferred_reason']=='analysis_source_changed'
        result=s.get(AnalysisResult,seen[-1]['analysis_result_id'])
        assert result.payload['status']=='superseded' and result.payload['error']=='analysis_source_changed'

def test_pipeline_api_is_bounded_read_only_and_not_activated_fallback(isolated,monkeypatch):
    from fastapi.testclient import TestClient
    from services.api.main import app
    at=int(time.time()*1000);iid='binance:perp:BTCUSDT'
    for n in range(3):
        context=pipeline.begin_result(iid,'trend','test-version-'+str(n),{},at+n)
        pipeline.complete_result(context,{'computed_at':at+n,'strategy_result':'not_applicable'},at+n+1)
    monkeypatch.setattr(signal_service,'scan_signals',lambda *args:(_ for _ in ()).throw(AssertionError('GET must not compute')))
    client=TestClient(app)
    reply=client.get('/v1/analysis-pipeline/'+iid+'?limit=2')
    assert reply.status_code==200 and len(reply.json()['results'])==2
    assert client.get('/v1/analysis-pipeline/'+iid+'?limit=51').status_code==422
    monkeypatch.setattr(diagnostics,'enabled',lambda:False)
    assert client.get('/v1/analysis-pipeline/'+iid).json()['state']=='not_activated'

def test_shadow_uses_existing_lane_without_publication(isolated,monkeypatch):
    from engine.models import Plan,SignalSnapshot,CurrentSignal
    at=int(time.time()*1000);iid='binance:perp:BTCUSDT'
    before={model.__tablename__:None for model in (Episode,Plan,SignalSnapshot,CurrentSignal)}
    with isolated.begin() as s:
        for model in (Episode,Plan,SignalSnapshot,CurrentSignal):before[model.__tablename__]=list(s.scalars(select(model.id)))
        pipeline.upsert(s,iid,'baseline_screen',iid,at,at+3600000,{'shadow':True},state='shadow')
    assert pipeline.submit_shadow(at)==1
    lease=jobs.take(at,work_kinds=['structure']);assert lease['shadow'] and lease['instrument_id']==iid
    summary=pipeline.analyze_shadow(iid,lease['version'],lease['demand_id'],at)
    assert summary['shadow'] and not summary['publication']
    jobs.finish(lease,at=at+1)
    with isolated() as s:
        for model in (Episode,Plan,SignalSnapshot,CurrentSignal):assert list(s.scalars(select(model.id)))==before[model.__tablename__]
        result=s.get(MarketAnalysisState,(iid,'baseline_screen'))
        assert result.strategy_result=='shadow_only'
    # A queued live task always precedes an older shadow task in the same lane.
    with isolated.begin() as s:
        row=s.get(Task,lease['id']);row.state='queued';row.available_at=at-10000
    jobs.submit(['live'],at=at)
    assert not jobs.take(at,work_kinds=['structure'])['shadow']

def test_schema_migration_is_additive_and_reversible():
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect
    from engine.db import engine
    migration=importlib.import_module('migrations.versions.p39_layered_analysis')
    assert engine.url.database.startswith('erji_p36_check_')
    # Dedicated schema in the already disposable cluster; never production.
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE SCHEMA p39_layer_migration')
        conn.exec_driver_sql('SET LOCAL search_path TO p39_layer_migration')
        conn.exec_driver_sql('CREATE TABLE instruments (id VARCHAR(180) PRIMARY KEY)')
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            assert all(inspect(conn).has_table(name,schema='p39_layer_migration') for name in ('analysis_demands','analysis_results','market_analysis_states'))
            migration.downgrade()
            assert not inspect(conn).has_table('analysis_demands',schema='p39_layer_migration')
            migration.upgrade()
            migration.downgrade()
        conn.exec_driver_sql('DROP SCHEMA p39_layer_migration CASCADE')

def test_trend_reuses_input_without_making_stale_candles_fresh(isolated,monkeypatch):
    iid='binance:perp:BTCUSDT';at=int(time.time()*1000)
    with isolated.begin() as s:
        s.add(Observation(id='trend-cached',instrument_id=iid,asset_id='BTC',direction='up',first_at=at,last_at=at,
            visible_at=at,status='observing',version=at,payload={}))
    calls=[]
    def fake_trend(rows,step,now):
        calls.append(step);return {'direction':'up','status':'ready','bars':300,'as_of':at}
    monkeypatch.setattr(analysis,'trend',fake_trend)
    first=analysis.analyze_market(iid,at)
    assert first['computed'] and len(calls)==6
    later=analysis.analyze_market(iid,at+300001)
    assert not later['computed'] and len(calls)==6 and later['computed_at']==at
    with isolated() as s:
        row=s.get(Observation,'trend-cached')
        assert row.payload['matrix']['5']['status']=='delayed'
        assert row.payload['analyzed_at']==at and row.payload['analysis_checked_at']==at+300001
