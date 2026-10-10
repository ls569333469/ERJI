import asyncio
from unittest.mock import Mock
from engine.pd2 import native_streams
from test_p25_signals import isolated


def test_cold_queue_does_not_starve_active_or_remain_unvisited_forever():
    from engine.pd2.collection_schedule import choose_due
    now=2000000000000;intervals={'active':30000,'cold':300000}
    assert choose_due(['active','cold'],{'active':now-60000},intervals,now,1,{'cold':now})==['active']
    later=now+900000
    assert choose_due(['active','cold'],{'active':later-60000},intervals,later,1,{'cold':now})==['cold']


def test_unqualified_newer_structure_cannot_hide_valid_candidate(isolated,monkeypatch):
    from test_p25_signals import seed,NOW
    from engine.pd2 import signal_projection as projection
    from engine.models import CurrentSignal
    monkeypatch.setattr(projection,'now_ms',lambda:NOW)
    seed(isolated,action='waiting')
    with isolated.begin() as s:
        good=s.get(CurrentSignal,'p25plan0');good.computed_at=NOW-1000
        s.add(CurrentSignal(id='aaa-newer',asset_id=good.asset_id,instrument_id=good.instrument_id,
            action='waiting',first_seen_at=NOW,computed_at=NOW,valid_until=NOW+10000,
            payload={**good.payload,'opportunity_eligible':False}))
    rows=projection.read('opportunities')['rows']
    assert len(rows)==1 and rows[0]['opportunity_eligible'] is True
    with isolated.begin() as s:
        s.get(CurrentSignal,'p25plan0').payload={**good.payload,'structure_valid_until':NOW-1}
    assert projection.read('opportunities')['rows']==[]


def test_old_ended_plan_does_not_suppress_new_pending_episode(isolated,monkeypatch):
    from types import SimpleNamespace as N
    from test_p25_signals import seed,NOW
    from engine.models import Campaign,Episode,Plan,SignalSnapshot
    from engine.pd2 import service,signal_projection,derivatives
    seed(isolated,action='waiting');iid='binance:perp:P25TEST0USDT'
    monkeypatch.setattr(service,'now_ms',lambda:NOW)
    market=N(instrument_id=iid,asset_id='P25TEST0',venue='binance',as_of=NOW,model_dump=lambda **kw:{'price':'1','as_of':NOW})
    monkeypatch.setattr(service,'list_markets',lambda ids:N(rows=[market]))
    monkeypatch.setattr(derivatives,'shared_inputs',lambda now:{})
    monkeypatch.setattr(derivatives,'context_for',lambda *args:{})
    published=[];monkeypatch.setattr(signal_projection,'publish',lambda rows:published.extend(rows))
    with isolated.begin() as s:
        s.add(Campaign(id='p33-old-campaign',asset_id='P25TEST0',first_seen_at=NOW-10000,
            first_seen_price=1,reference_instrument_id=iid,state='active'));s.flush()
        for eid,phase in [('p33-old-episode','ended'),('p33-new-pending','structure_pending')]:
            s.add(Episode(id=eid,campaign_id='p33-old-campaign',strategy_id='box',phase=phase,
                rule_version='test',evidence={'trigger':True} if phase=='ended' else {}))
        s.flush();s.add(Plan(id='p33-old-plan',episode_id='p33-old-episode',execution_instrument_id=iid,
            revision=1,expires_at=NOW-1,payload={}));s.flush()
        s.add(SignalSnapshot(id='p33-old-snapshot',plan_id='p33-old-plan',as_of=NOW-10000,payload={
            'asset_id':'P25TEST0','instrument_id':iid,'as_of':NOW-10000,'action':{'code':'ended','reason':'结构结束'},
            'episode_id':'p33-old-episode','plan_id':'p33-old-plan','phase':'ended','phase_label':'结束',
            'first_seen_at':NOW-10000,'first_seen_price':'1','market':{'price':'1'},'evidence':{},
            'trade_thesis':{},'opportunity_plan':{},'execution':{},'timeframe_assessments':[]}))
    service.decision_list()
    assert {'p33-old-episode','p33-new-pending'}<={r['id'] for r in published}


def test_receiver_recovers_when_database_and_health_writes_both_fail(monkeypatch):
    attempts=[];waits=[]
    async def receiver():
        attempts.append(1)
        if len(attempts)==1:raise TimeoutError('temporary database failure')
        raise asyncio.CancelledError()
    async def sleep(seconds):waits.append(seconds)
    monkeypatch.setattr(native_streams,'heartbeat',Mock(side_effect=TimeoutError()))
    monkeypatch.setattr(native_streams.asyncio,'sleep',sleep)
    async def check():
        try:await native_streams.supervise_stream('test',receiver)
        except asyncio.CancelledError:pass
    asyncio.run(check())
    assert len(attempts)==2 and len(waits)==1 and 4<=waits[0]<=6


def test_crashed_receiver_does_not_claim_unobserved_future_coverage():
    from types import SimpleNamespace
    records=[SimpleNamespace(updated_at=200,payload={'segments':[{'start':100,'end':None,'instruments':['x']}]})]
    assert native_streams.proven_segments(records,900)==[{'start':100,'end':200,'instruments':['x']}]


def test_spot_decision_freezes_contract_daily_oi_endpoints_outside_recent_page(isolated):
    from engine.models import Asset,Instrument,Candle,RawEvent,OiPoint
    from engine.pd2.service import decision_derivative_evidence,referenced_derivative_history
    day=86400000;now=20000*day+12*3600000;start=19998*day
    spot='binance_spot:spot:P33DAYUSDT';perp='binance:perp:P33DAYUSDT'
    with isolated.begin() as s:
        s.add(Asset(id='P33DAY',symbol='P33DAY',known_at=start));s.flush()
        for iid,venue,kind in [(spot,'binance_spot','spot'),(perp,'binance','linear_perpetual')]:
            s.add(Instrument(id=iid,asset_id='P33DAY',venue=venue,native_symbol='P33DAYUSDT',market_type=kind,quote_asset='USDT',spec={}))
        s.add(RawEvent(id='p33-daily-source',source='test',endpoint='test',received_at=start,payload={}));s.flush()
        for i in range(2):
            s.add(Candle(instrument_id=perp,resolution='1D',open_time=start+i*day,close_time=start+(i+1)*day,is_closed=True,open=10,close=12,high=13,low=9,base_volume=1,quote_volume=10,received_at=start+(i+1)*day,raw_ref='p33-daily-source'))
        for i in range(3):
            s.add(OiPoint(instrument_id=perp,event_time=start+i*day,base_quantity=100+i*10,notional_quote=1000,quote_asset='USDT',received_at=now-1,raw_ref='p33-daily-source'))
        for i in range(1001):
            s.add(OiPoint(instrument_id=perp,event_time=now-1100000+i*1000,base_quantity=130,notional_quote=1300,quote_asset='USDT',received_at=now-1,raw_ref='p33-daily-source'))
    context={'derivatives_reference_market':{'instrument_id':perp}}
    history,path=decision_derivative_evidence(spot,context,{},now)
    assert history['instrument_id']==perp and len(history['oi'])==1003
    assert path['path']=='增仓上涨' and len(path['windows'])==2
    assert path['price_market_id']==path['oi_market_id']==perp
    assert referenced_derivative_history(spot,now)['instrument_id']==perp


def test_identity_requires_exact_chain_address_and_rejects_conflicts():
    from types import SimpleNamespace as N
    from engine.pd2.identity import exact_contract_links
    instrument=N(asset_id='chain:token',spec={'chain_id':'56','contract_address':'0xAbC'})
    row={'id':1,'symbol':'SAME','is_active':1,'platform':{'slug':'bnb','token_address':'0xabc'}}
    assert exact_contract_links([instrument],[row])[0]['external_id']=='1'
    assert exact_contract_links([instrument],[{**row,'platform':{'slug':'ethereum','token_address':'0xabc'}}])==[]
    assert exact_contract_links([instrument],[row,{**row,'id':2}])==[]
    sol=N(asset_id='sol:token',spec={'chain_id':'501','contract_address':'AbC'})
    assert exact_contract_links([sol],[{**row,'platform':{'slug':'solana','token_address':'abc'}}])==[]


def test_closed_frames_are_not_polled_again_before_next_close(isolated,monkeypatch):
    from engine import repository
    from engine.pd2 import collection_schedule
    from engine.models import Asset,Instrument,Candle,RawEvent
    now=20000*86400000+12*3600000+600000;iid='binance:perp:P33DUEUSDT';acks=[]
    monkeypatch.setattr(repository,'now_ms',lambda:now)
    monkeypatch.setattr(collection_schedule,'due',lambda *a,**kw:['P33DUEUSDT'])
    monkeypatch.setattr(collection_schedule,'acknowledge',lambda *a:acks.append(a))
    with isolated.begin() as s:
        s.add(Asset(id='P33DUE',symbol='P33DUE',known_at=now));s.flush()
        s.add(Instrument(id=iid,asset_id='P33DUE',venue='binance',native_symbol='P33DUEUSDT',market_type='linear_perpetual',quote_asset='USDT',spec={}));s.flush()
        s.add(RawEvent(id='p33-due-source',source='test',endpoint='test',received_at=now,payload={}));s.flush()
        for tf,duration in [('60',3600000),('240',14400000),('1D',86400000),('1W',604800000)]:
            offset=345600000 if tf=='1W' else 0;closed=(now-offset)//duration*duration+offset
            for n in range(300):
                s.add(Candle(instrument_id=iid,resolution=tf,open_time=closed-(n+1)*duration,close_time=closed-n*duration,is_closed=True,open=10,close=10,high=11,low=9,base_volume=1,quote_volume=10,received_at=closed,raw_ref='p33-due-source'))
    # A forming bar must not evict the oldest of the 300 required closed bars.
    with isolated.begin() as session:
        step=14400000;closed=now//step*step
        session.add(Candle(instrument_id=iid,resolution='240',open_time=closed,close_time=closed+step,is_closed=False,open=10,close=10,high=11,low=9,base_volume=1,quote_volume=10,received_at=now,raw_ref='p33-due-source'))
    assert repository.candle_targets('binance')==[]
    assert acks==[('binance',['P33DUEUSDT'],'candles',now)]
    monkeypatch.setattr(repository,'now_ms',lambda:now+3600000)
    assert [t[2] for t in repository.candle_targets('binance')]==['60']
    monkeypatch.setattr(repository,'now_ms',lambda:now+14400000)
    assert [t[2] for t in repository.candle_targets('binance')]==['240','60']


def test_fibonacci_c_confirmation_projection_invalidation_and_gaps():
    from copy import deepcopy
    from test_event_overlays import bars
    from engine.pd2.event_overlays import event_fibonacci
    values=[8,7,4,7,8,12,9,8,9,10,9,8,9,11,14,15]
    rows=bars(values);trigger=rows[8]['close_time']
    initial=event_fibonacci(rows[:9],trigger,trigger)
    before=event_fibonacci(rows[:13],trigger,rows[12]['close_time'],initial)
    assert len(before['anchors'])==2
    after=event_fibonacci(rows,trigger,rows[-1]['close_time'],initial)
    assert after['status']=='extension' and len(after['anchors'])==3
    assert [v['price'] for v in after['levels'] if v['kind']=='conditional_extension']==['17','23.180']
    failed=deepcopy(rows);failed[-1]['low']='2'
    assert event_fibonacci(failed,trigger,failed[-1]['close_time'],after)['status']=='invalidated'
    assert event_fibonacci(rows[:10]+rows[11:],trigger,rows[-1]['close_time'],initial)['status']=='source_gap'
    assert event_fibonacci(rows[:12],trigger,rows[11]['close_time'],after)['anchors']==[]


def test_confirmed_box_last_source_bar_and_monotonic_protection_history():
    from test_chart_contract import snapshot
    from engine.pd2.chart_contract import drawing_contract
    s=snapshot();s['evidence']['box'].update(start_time=14400000,end_time=21*14400000)
    s['evidence']['trigger'].update(open_time=21*14400000,close_time=22*14400000)
    s['as_of']=23*14400000;s['valid_until']=24*14400000
    assert drawing_contract(s)['anchors'][0]['last_open_time']==20*14400000
    s['tracking']={'protection_history':[{'price':'1.8','observed_at':22*14400000},{'price':'1.7','observed_at':22*14400000+1000}]}
    assert drawing_contract(s)['protection_history']==[]


def test_inherited_stop_records_effective_time_and_never_moves_backwards():
    from engine.strategies.p33_runtime import inherit_protection
    event={'initial':{'protection':'1'},'protection_history':[{'price':'1','observed_at':100}]}
    inherit_protection(event,'1.2',200)
    inherit_protection(event,'1.3',200)
    inherit_protection(event,'1.1',300)
    assert event['current_protection']=='1.3'
    assert event['protection_history']==[{'price':'1','observed_at':100},{'price':'1.3','observed_at':200}]


def test_alpha_quote_commits_before_slow_next_symbol_without_faking_source_time(monkeypatch):
    from engine.adapters.expanded_markets import BinanceCashAdapter
    from engine.adapters.base import now_ms
    from engine.contracts import RuntimeConfig
    from engine.pd2 import collection_schedule
    monkeypatch.setattr(collection_schedule,'due',lambda venue,natives,**kw:natives)
    async def scenario():
        adapter=BinanceCashAdapter(alpha=True);adapter.catalog_at=now_ms();saved=[]
        adapter.catalog=[{'symbol':base+'USDT','baseAsset':base,'quoteAsset':'USDT'} for base in ('ALPHA_1','ALPHA_2')]
        adapter.tokens={base:{'chainId':'56','contractAddress':'0x'+str(n),'symbol':base,'circulatingSupply':'2000000','price':'1','liquidity':'1000000','marketCap':'2000000'} for n,base in enumerate(('ALPHA_1','ALPHA_2'))}
        waiting=asyncio.Event();release=asyncio.Event()
        async def data(batch,path,params):
            if params['symbol']=='ALPHA_2USDT':waiting.set();await release.wait()
            return {'lastPrice':'1','closeTime':100,'quoteVolume':'1000'},'raw',1000
        async def persist(part):saved.extend(part.states)
        adapter.data=data
        try:
            task=asyncio.create_task(adapter.collect(RuntimeConfig(),history=False,on_batch=persist))
            await waiting.wait()
            assert len(saved)==1 and saved[0]['as_of']==100 and not task.done()
            release.set();result=await task
            assert len(saved)==2 and result.states==[]
        finally:await adapter.close()
    asyncio.run(scenario())
