from copy import deepcopy
from decimal import Decimal as D
from sqlalchemy import event,select
from engine.models import Asset,Instrument,MarketState,DataCapability,OiPoint,Observation,CurrentSignal,RawEvent
from engine.pd2 import current_market_fields as fields,binance_facts
from engine.p37 import worker,store
from engine.p37.detector import Detector,DEFAULTS
from test_p25_signals import isolated

NOW=2000000040000


def native(session,asset,venue='binance',**spec):
    if not session.get(Asset,asset):session.add(Asset(id=asset,symbol=asset,known_at=1));session.flush()
    iid=f'{venue}:{"spot" if venue!="binance" else "perp"}:{asset}USDT'
    session.add(Instrument(id=iid,asset_id=asset,venue=venue,native_symbol=asset+'USDT',quote_asset='USDT',
        market_type='spot' if venue!='binance' else 'linear_perpetual',spec={'base_asset':asset,'status':'TRADING',**spec}))
    session.flush();return iid


def state(session,iid,at=NOW-1000,**payload):
    data=dict(as_of=NOW-1000,price='2',price_kind='last',oi_base='200',oi_notional_quote='400',oi_status='stale',
        oi_as_of=at,funding_rate='0.0001',funding_settled_at=NOW-3600000,funding_interval_seconds=28800,funding_status='stale')
    data.update(payload)
    session.add(MarketState(instrument_id=iid,as_of=data['as_of'],payload=data))
    session.flush()


def fx(session):
    session.add(DataCapability(key='binance:fx:USDTUSD',provider='binance',capability='fx',subject='USDTUSD',
        status='fresh',observed_at=NOW-1000,received_at=NOW-1000,expires_at=NOW+10000,payload={'symbol':'USDTUSD','index':'1'}))
    session.flush()


def point(session,iid,at,q,received=None):
    if not session.get(RawEvent,'test-old'):
        session.add(RawEvent(id='test-old',source='binance',endpoint='test:oi',received_at=NOW-1,payload={'test_fixture':True}));session.flush()
    session.add(OiPoint(instrument_id=iid,event_time=at,received_at=received or NOW-1,base_quantity=D(q),
        notional_quote=D(q)*2,quote_asset='USDT',raw_ref='test-old'))
    session.flush()


def test_current_list_replaces_old_derivatives_but_preserves_decision_fields(isolated):
    with isolated.begin() as s:
        iid=native(s,'NEWFIELDS');state(s,iid);fx(s);point(s,iid,NOW-901000,'100')
        row={'instrument_id':iid,'asset_id':'NEWFIELDS','oi_usd':None,'oi_status':'primary_stale',
            'funding_rate':None,'action_code':'waiting','entry_zone':['1','2'],'reason':'frozen reason','decision_as_of':1}
        frozen=deepcopy(row)
        fields.enrich(s,[row],NOW)
        assert row['oi_usd']=='400' and row['oi_status']=='fresh'
        assert row['oi_as_of']==NOW-1000 and row['funding_status']=='fresh' and row['funding_rate']=='0.0001'
        assert row['valuation_status']=='binance_supply_not_provided' and row['oi_cap_ratio'] is None
        for key in ('action_code','entry_zone','reason','decision_as_of'):assert row[key]==frozen[key]
        changes=fields.quantity_changes(s,{iid:s.get(MarketState,iid).payload},NOW)
        assert changes[iid][0]['value_pct']=='100' and changes[iid][0]['end_at']==NOW-1000


def test_recorded_fresh_is_recomputed_and_old_notional_never_becomes_current_usd(isolated):
    with isolated.begin() as s:
        iid=native(s,'OLDSTATUS');state(s,iid,at=NOW-600001,oi_status='fresh',funding_settled_at=NOW-28800000-180001,funding_status='fresh');fx(s)
        row={'instrument_id':iid,'asset_id':'OLDSTATUS','oi_usd':'999','oi_cap_ratio':'99','visible_at':1,'oi_change':999}
        fields.enrich(s,[row],NOW)
        assert row['oi_status']=='stale' and row['oi_usd'] is None and row['oi_cap_ratio'] is None
        assert row['native_oi']['notional_quote']=='400' and row['native_oi']['status']=='stale'
        assert row['funding_status']=='stale' and row['oi_change'] is None and row['oi_change_status']=='stale'


def test_spot_references_same_asset_contract_but_own_oi15_is_not_applicable(isolated):
    with isolated.begin() as s:
        iid=native(s,'LINKED');spot=native(s,'LINKED','binance_spot');state(s,iid);state(s,spot);fx(s)
        row={'instrument_id':spot,'asset_id':'LINKED','visible_at':1,'oi_change':999}
        fields.enrich(s,[row],NOW)
        assert row['native_oi']['instrument_id']==iid and row['oi_usd']=='400'
        assert row['oi_status']=='not_applicable' and row['oi_change_status']=='not_applicable' and row['oi_change'] is None


def test_names_and_multipliers_cannot_invent_contract_identity(isolated):
    with isolated.begin() as s:
        spot=native(s,'FAKE','binance_spot');iid=native(s,'1000FAKE');state(s,spot);state(s,iid);fx(s)
        row={'instrument_id':spot,'asset_id':'FAKE','oi_usd':'999','contract_status':'verified'}
        fields.enrich(s,[row],NOW)
        assert row['contract_status']=='unmapped' and row['native_oi']['instrument_id'] is None and row['oi_usd'] is None


def test_delisted_contract_is_never_the_live_reference(isolated):
    with isolated.begin() as s:
        spot=native(s,'RETIRED','binance_spot');iid=native(s,'RETIRED',status='delisted_or_ineligible');state(s,spot);state(s,iid);fx(s)
        row={'instrument_id':spot,'asset_id':'RETIRED'};fields.enrich(s,[row],NOW)
        assert row['contract_status']=='unmapped'


def test_web_supply_uses_receipt_time_reference_and_current_price_not_web_ticker(isolated):
    payload=binance_facts.product_supplies({'code':'000000','data':[
        {'b':'SUPPLY','s':'SUPPLYUSDT','st':'TRADING','cs':'100','c':'999','ts':'0.01','an':'Supply'},
        {'b':'SUPPLY','s':'SUPPLYBTC','st':'TRADING','cs':'100'},
        {'b':'CONFLICT','s':'CONFLICTUSDT','st':'TRADING','cs':'100'},
        {'b':'CONFLICT','s':'CONFLICTBTC','st':'TRADING','cs':'200'},
        {'b':'FDVONLY','st':'TRADING','totalSupply':'999','cs':None}]})
    assert 'CONFLICT' not in payload['assets'] and 'FDVONLY' not in payload['assets'] and not payload['action_eligible']
    with isolated.begin() as s:
        iid=native(s,'SUPPLY');state(s,iid);fx(s)
        s.add(DataCapability(key='binance:supply:web_products',provider='binance',capability='supply',subject='web_products',
            status='source_time_unknown',received_at=NOW-1000,observed_at=None,expires_at=NOW+86400000,payload=payload));s.flush()
        row={'instrument_id':iid,'asset_id':'SUPPLY'};fields.enrich(s,[row],NOW)
        assert row['circulating_cap']=='200' and row['oi_cap_ratio']=='2'
        assert row['valuation_status']=='reference' and row['valuation_observed_at'] is None and row['valuation_received_at']==NOW-1000


def test_batch_uses_constant_queries_for_duplicate_events_and_rejects_future_known_points(isolated):
    with isolated.begin() as s:
        iid=native(s,'BATCHED');state(s,iid);fx(s);point(s,iid,NOW-901000,'100',received=NOW+1000)
        rows=[{'instrument_id':iid,'asset_id':'BATCHED','visible_at':1,'oi_change':999} for _ in range(100)]
        count=[];connection=s.connection()
        def statement(*args):count.append(1)
        event.listen(connection,'before_cursor_execute',statement)
        try:fields.enrich(s,rows,NOW)
        finally:event.remove(connection,'before_cursor_execute',statement)
        assert len(count)<=5 and all(r['oi_change'] is None and r['oi_change_status']=='insufficient_history' for r in rows)


def test_seed_native_oi_reuses_real_endpoints_without_filling_shutdown_gap(isolated,monkeypatch):
    detector=Detector();detector.catalog={'binance:perp:SEEDOIUSDT':{'venue':'binance'}}
    monkeypatch.setattr(worker,'detector',detector)
    with isolated.begin() as s:
        iid=native(s,'SEEDOI');point(s,iid,NOW-901000,'100');point(s,iid,NOW-1000,'110')
        point(s,iid,NOW+1000,'999',received=NOW+1000)
    worker.seed_oi_memory(NOW)
    assert detector.facts(iid,NOW)['oi_change']==10
    worker.seed_oi_memory(NOW+3600000)
    assert detector.facts(iid,NOW+3600000)['oi_change'] is None and len(detector.oi[iid])==2
    assert not detector.prices


def test_tracking_bounded_batches_make_progress_and_oi_updates_without_anomaly(isolated,monkeypatch):
    detector=Detector();monkeypatch.setattr('engine.p37.detector.detector',detector)
    with isolated.begin() as s:
        iid=native(s,'TRACKOI');state(s,iid)
        for n in range(140):s.add(Observation(id=f'track-{n:03d}',instrument_id=iid,asset_id='TRACKOI',direction='up',first_at=NOW,
            visible_at=NOW,last_at=NOW,status='observing',version=NOW,payload={'symbol':'TRACKOI','oi_change':999,'tracked_at':0}))
    detector.oi_point(iid,NOW-901000,'100');detector.oi_point(iid,NOW-1000,'110')
    for expected in (64,128,140):
        store.refresh_tracking(NOW,DEFAULTS)
        with isolated() as s:
            rows=list(s.scalars(select(Observation).where(Observation.asset_id=='TRACKOI')))
            done=[r for r in rows if r.payload.get('tracked_at')==NOW]
            assert len(done)==expected and all(r.payload['oi_change']==10 and r.payload['oi_change_status']=='available' for r in done)


def test_funding_clock_handles_unknown_cycles_future_time_and_zero_rate():
    assert fields.funding_clock({'funding_settled_at':NOW,'funding_interval_seconds':3600,'funding_rate':'0'},NOW)=='fresh'
    assert fields.funding_clock({'funding_settled_at':NOW,'funding_rate':'0'},NOW)=='interval_unknown'
    assert fields.funding_clock({'funding_settled_at':NOW+1,'funding_rate':'0'},NOW)=='clock_invalid'


def test_supply_collector_persists_provenance_caches_and_backs_off(isolated,monkeypatch):
    import asyncio
    from engine.pd2 import storage
    calls=[];clock=[NOW]
    monkeypatch.setattr(binance_facts,'now_ms',lambda:clock[0])
    monkeypatch.setattr(storage,'now_ms',lambda:clock[0])
    async def request(self,batch,method,url,**kwargs):
        calls.append(url)
        if len(calls)>1:raise ValueError('fixture_failure')
        payload={'code':'000000','data':[{'b':'COLLECTSUPPLY','s':'COLLECTSUPPLYUSDT','st':'TRADING','cs':'100'}]}
        batch.raw.append(dict(id='supply-fixture-raw',source='binance',endpoint=url,received_at=clock[0],payload=payload))
        return payload,'supply-fixture-raw',clock[0]
    monkeypatch.setattr(binance_facts.BinanceReferenceAdapter,'request',request)
    async def run():
        adapter=binance_facts.BinanceReferenceAdapter()
        try:
            assert await adapter.collect_supply()=='source_time_unknown_reference'
            assert await adapter.collect_supply()=='cached_reference'
            clock[0]+=21600000
            assert await adapter.collect_supply()=='ValueError'
            assert await adapter.collect_supply()=='cached_reference'
        finally:await adapter.close()
    asyncio.run(run())
    with isolated() as s:
        row=s.get(DataCapability,'binance:supply:web_products')
        assert row.observed_at is None and row.status=='source_error' and row.received_at==NOW
        assert row.next_refresh_at==NOW+21600000+3600000
        assert row.payload['assets']['COLLECTSUPPLY']['supply']=='100'
        assert s.get(RawEvent,row.payload['source_raw_ref']).payload['code']=='000000'
        assert len(calls)==2 and calls==[binance_facts.WEB_PRODUCTS]*2


def test_history_endpoints_accept_five_minute_grid_but_keep_oi15_tight(isolated):
    with isolated.begin() as s:
        iid=native(s,'GRID');state(s,iid)
        point(s,iid,NOW-1000-86400000-180000,'100')
        point(s,iid,NOW-1000-900000-180000,'100')
        result=fields.quantity_changes(s,{iid:s.get(MarketState,iid).payload},NOW)[iid]
        assert result[0]['status']=='insufficient_history'
        daily=next(x for x in result if x['period']=='1D')
        assert daily['status']=='available' and daily['value_pct']=='100'
        assert daily['duration_ms']==86400000+180000


def test_candle_seed_batches_each_instrument_without_restoring_tick_windows(isolated,monkeypatch):
    from engine.models import Candle
    detector=Detector();monkeypatch.setattr(worker,'detector',detector)
    with isolated.begin() as s:
        ids=[native(s,'SEEDBARS'+str(n)) for n in range(2)]
        detector.catalog={iid:{'venue':'binance'} for iid in ids}
        s.add(RawEvent(id='seed-bars-raw',source='binance',endpoint='test:bars',received_at=NOW,payload={'test_fixture':True}));s.flush()
        for iid in ids:
            for n in range(-12,0):s.add(Candle(instrument_id=iid,resolution='5',open_time=NOW+n*300000,
                close_time=NOW+(n+1)*300000,is_closed=True,open=1,high=2,low=1,close=2,
                base_volume=1,quote_volume=2,received_at=NOW,raw_ref='seed-bars-raw'))
    worker.seed_memory()
    assert all(len(detector.bars[iid])==11 for iid in ids)
    assert not detector.prices
