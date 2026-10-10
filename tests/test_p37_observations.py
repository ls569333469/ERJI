from decimal import Decimal as D
from engine.p37.detector import Detector,DEFAULTS
from engine.p37.analysis import trend,aggregate

def test_price_window_gap_is_not_a_trigger():
    d=Detector();d.catalog={'x':{}}
    d.price('x',1000,'100');d.price('x',301000,'110')
    assert not d.facts('x',301000)['reasons']

def test_five_minute_not_one_minute():
    d=Detector();d.catalog={'x':{}}
    for at in range(1000,302000,1000):d.price('x',at,'104' if at>290000 else '100')
    facts=d.facts('x',301000)
    assert facts['reasons'][0]['window']==5
    assert all(r['window']!=1 for r in facts['reasons'])

def test_volume_baseline_excludes_current_and_can_trigger_intrabar():
    d=Detector();d.catalog={'x':{}}
    for n in range(11):d.bar('x',dict(open_time=n*300000,open='1',close='1.1',volume=400000 if n==10 else 100000,closed=n<10,at=3000000))
    f=d.facts('x',3000000)
    assert f['rvol']==4
    assert f['reasons'][0]['intrabar']

def test_comovement_requires_valid_breadth_and_independent_facts():
    d=Detector();d.config={**DEFAULTS,'breadth_min':2}
    for iid in ['binance:perp:BTCUSDT','x','y']:d.catalog[iid]=dict(venue='binance',asset_id=iid,liquidity=2000000)
    all_facts={k:dict(changes={'5':2,'15':None}) for k in d.catalog}
    f=dict(reasons=[dict(kind='price',direction='up',window=5,value=3)],rvol=1,oi_change=0)
    assert d.classify('x',f,all_facts)=='market_comovement'
    f['oi_change']=None
    assert d.classify('x',f,all_facts)=='unknown'
    f['reasons'].append(dict(kind='volume',direction='up',window=5,value=4))
    assert d.classify('x',f,all_facts)=='visible'

def test_native_oi_quantity_does_not_change_with_price():
    d=Detector();d.catalog={'x':{}}
    d.oi_point('x',1000,'100');d.oi_point('x',901000,'100')
    assert d.facts('x',901000)['oi_change']==0

def test_partial_aggregates_and_future_bars_not_called_ready():
    assert aggregate([dict(open_time=0,close='1',volume=D(1))],900000)==[]
    rows=[dict(open_time=i*300000,close_time=(i+1)*300000,close=str(i+1),volume=D(1)) for i in range(300)]
    assert trend(rows,300000,90000000)['direction']=='up'
    assert trend(rows[:-1],300000,90000000)['status']=='delayed'


from test_p25_signals import isolated
from sqlalchemy import select

def test_observation_promotion_freezes_visible_basis_and_preserves_first(isolated):
    from engine.p37 import store
    from engine.models import Observation
    from sqlalchemy import select
    now=2000000000000
    meta={'binance:perp:XUSDT':dict(asset_id='X',symbol='XUSDT',state={'price':'10'})}
    f=dict(reasons=[dict(kind='price',window=5,value=3,direction='up',source_at=now)],rvol=1,oi_change=0,changes={'5':3},basis_price='10',basis_at=now,basis_source='discovery_stream')
    store.persist([('binance:perp:XUSDT',f,'market_comovement')],meta,now,DEFAULTS)
    assert store.read_observations()['rows']==[]
    f['reasons'][0]['source_at']=now+1000;meta['binance:perp:XUSDT']['state']['price']='12';f.update(basis_price='12',basis_at=now+1000)
    store.persist([('binance:perp:XUSDT',f,'visible')],meta,now+1000,DEFAULTS)
    with isolated() as s:
        row=s.scalar(select(Observation).where(Observation.asset_id=='X'))
        assert row.first_at==now and row.visible_at==now+1000
        assert row.payload['first_price']=='10' and row.payload['visible_price']=='12'
    store.persist([('binance:perp:XUSDT',f,'market_comovement')],meta,now+2000,DEFAULTS)
    assert len(store.read_observations()['rows'])==1

def test_incremental_publish_does_not_delete_other_market(isolated,monkeypatch):
    from engine.models import CurrentSignal
    from engine.pd2.signal_projection import publish
    from test_p25_signals import seed,NOW
    seed(isolated,action='waiting')
    with isolated() as s:
        before=set(s.scalars(select(CurrentSignal.id)))
    publish([],scope=['binance:perp:NOT_PRESENTUSDT'])
    with isolated() as s:assert set(s.scalars(select(CurrentSignal.id)))==before

def test_detector_duplicate_and_out_of_order_do_not_rewind():
    d=Detector();d.catalog={'x':{}}
    d.price('x',2000,'10');d.price('x',2000,'50');d.price('x',1000,'1')
    assert list(d.prices['x'])==[(2000,'10')]


def test_api_contract_and_config_validation(isolated,monkeypatch):
    from services.api import main
    from services.api import observations
    from engine.p37 import store
    monkeypatch.setattr(main,'Session',isolated)
    monkeypatch.setattr(observations,'Session',isolated)
    from fastapi.testclient import TestClient
    client=TestClient(main.app)
    result=client.get('/v1/anomaly-observations')
    assert result.status_code==200
    assert {'rows','counts','discovery','unique_assets'}<=set(result.json())
    headers={'x-erji-client':'local-ui'}
    assert client.put('/v1/anomaly-observations/config',headers=headers,json={'capacity':0}).status_code==422
    assert client.put('/v1/anomaly-observations/config',headers=headers,json={'capacity':12}).status_code==200
    assert client.get('/v1/anomaly-observations/config').json()['capacity']==12

def test_old_scoped_snapshot_cannot_delete_newer_projection(isolated):
    from test_p25_signals import seed,NOW
    from engine.pd2.signal_projection import publish
    from engine.models import CurrentSignal
    seed(isolated)
    publish([],scope=['binance:perp:P25TEST0USDT'],built_at=NOW-1)
    with isolated() as s:assert s.get(CurrentSignal,'p25plan0') is not None


def test_missing_volume_does_not_become_zero():
    rows=[dict(open_time=i*300000,close_time=(i+1)*300000,close=str(i+1),volume=None) for i in range(300)]
    assert trend(rows,300000,90000000)['rvol20'] is None
    assert aggregate(rows[:3],900000)[0]['volume'] is None


def test_cooling_archive_and_new_event_keep_original(isolated):
    from engine.p37 import store
    from engine.models import Observation
    now=2000000000000;iid='binance:perp:LIFECYCLEUSDT'
    meta={iid:dict(asset_id='LIFECYCLE',symbol='LIFECYCLEUSDT',state={'price':'10'})}
    f=dict(reasons=[dict(kind='price',window=5,value=3,direction='up',source_at=now)],rvol=None,oi_change=None,changes={'5':3})
    store.persist([(iid,f,'visible')],meta,now,DEFAULTS)
    store.refresh_tracking(now+DEFAULTS['cool_ms'],DEFAULTS)
    with isolated() as s:
        old=s.scalar(select(Observation).where(Observation.instrument_id==iid));ident=old.id
        assert old.status=='cooling' and old.payload['first_reasons']==f['reasons']
    store.refresh_tracking(now+DEFAULTS['archive_ms'],DEFAULTS)
    later=now+DEFAULTS['archive_ms']+1000;f['reasons'][0]['source_at']=later
    store.persist([(iid,f,'visible')],meta,later,DEFAULTS)
    with isolated() as s:
        assert s.get(Observation,ident).status=='archived'
        all_rows=list(s.scalars(select(Observation).where(Observation.instrument_id==iid)))
        assert len(all_rows)==2
        assert next(r for r in all_rows if r.id!=ident).payload['previous_event_id']==ident


def test_oi_trigger_survives_missing_price_and_incremental_contract(isolated):
    from engine.p37 import store
    from engine.p37.contracts import ObservationList
    import time
    now=int(time.time()*1000);iid='binance:perp:OIONLYUSDT'
    meta={iid:dict(asset_id='OIONLY',symbol='OIONLYUSDT',state={'price':None})}
    f=dict(reasons=[dict(kind='oi',window=15,value=6,direction='up',source_at=now)],rvol=None,oi_change=6,changes={'5':None})
    store.persist([(iid,f,'visible')],meta,now,DEFAULTS)
    first=ObservationList.model_validate(store.read_observations())
    assert first.rows[0].visible_price is None
    delta=ObservationList.model_validate(store.read_observations(since=first.cursor))
    assert delta.incremental and delta.cursor==first.cursor and delta.asset_ids==['OIONLY']
    assert all(r.data_status=='delayed' for r in delta.rows)  # stale rows refresh their status without hiding


def test_deep_capacity_never_drops_observations(isolated):
    from engine.p37.worker import select_work
    from engine.models import Observation
    now=2000000000000
    with isolated.begin() as s:
        from test_p39_remaining import native_market
        for n in range(60):
            native_market(s,f'binance:perp:T{n}USDT',f'T{n}')
            s.add(Observation(id=f'capacity{n}',instrument_id=f'binance:perp:T{n}USDT',asset_id=f'T{n}',direction='up',first_at=now,visible_at=now,last_at=now,status='observing',version=now,payload={'symbol':f'T{n}USDT'}))
    chosen,overflow=select_work(now,50)
    assert len(chosen)==50 and overflow==0
    with isolated() as s:assert len(list(s.scalars(select(Observation))))==60


def test_dispatch_does_not_wait_for_slow_member():
    import asyncio
    from engine.pd2.continuous_collection import dispatch
    async def exercise():
        queue=['slow','fast','next'];release=asyncio.Event();done=[]
        async def choose(slots):
            picked=queue[:slots];del queue[:slots];return picked
        async def execute(key):
            if key=='slow':await release.wait()
            if key=='next':release.set()
            done.append(key)
        async def enabled():return len(done)<3
        await asyncio.wait_for(dispatch(choose,execute,enabled,2),2)
        assert done.index('next')<done.index('slow')
    asyncio.run(exercise())


def test_spot_comovement_unknown_contract_oi_preserves_observation():
    d=Detector();d.config={**DEFAULTS,'breadth_min':1}
    d.catalog={'spot':dict(venue='binance_spot',asset_id='X',liquidity=2000000),'future':dict(venue='binance',asset_id='X',liquidity=2000000)}
    f=dict(reasons=[dict(kind='price',direction='up',window=5,value=3)],rvol=1,oi_change=None)
    assert d.classify('spot',f,{'future':{'oi_change':None}})=='unknown'
    assert d.classify('spot',f,{'future':{'oi_change':6}})=='visible'


def test_window_reservation_survives_single_slot_completions():
    from engine.p37.worker import choose_window_slots
    turn=0;picked=[]
    for _ in range(8):
        items,turn=choose_window_slots(['ordinary','observer'],{'observer'},1,turn)
        picked.extend(items)
    assert picked.count('observer')==6 and picked.count('ordinary')==2
