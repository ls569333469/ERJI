import asyncio
from types import SimpleNamespace as N
from sqlalchemy import select
from engine.models import DataCapability, RawEvent
from engine.pd2 import storage, derivative_collection
from engine.pd2.collection_schedule import signal_interval
from engine.pd2.identity import contract_key, exact_contract_links
from test_p25_signals import isolated

def test_failed_attempt_preserves_value_and_raw_lineage(isolated,monkeypatch):
    clock=[2000000000000];monkeypatch.setattr(storage,'now_ms',lambda:clock[0])
    good=storage.put('test','oi','p34',{'value':'100'},clock[0]-1000,300000)
    before=storage.get('test','oi','p34');clock[0]+=10000
    bad=storage.put('test','oi','p34',{},None,0,status='source_error',reason='timeout')
    failed=storage.get('test','oi','p34')
    assert failed['status']=='source_error' and failed['payload']=={'value':'100'}
    assert failed['raw_ref']==failed['last_success_raw_ref']==good
    assert failed['last_attempt_raw_ref']==bad and failed['last_attempt_at']==clock[0]
    assert failed['received_at']==before['received_at'] and failed['expires_at']==before['expires_at']
    with isolated() as s:
        assert s.get(RawEvent,good).payload==failed['payload']
        assert s.get(RawEvent,bad).payload=={}
    clock[0]+=10000
    restored=storage.put('test','oi','p34',{'value':'120'},clock[0],300000)
    assert storage.get('test','oi','p34')['raw_ref']==restored

def test_unknown_source_time_replaces_response_but_never_becomes_fresh(isolated,monkeypatch):
    monkeypatch.setattr(storage,'now_ms',lambda:2000000000000)
    storage.put('test','pool','p34',{'value':1},1999999999000,300000)
    ref=storage.put('test','pool','p34',{'value':2},None,300000)
    value=storage.get('test','pool','p34')
    assert value['status']=='source_time_unknown' and value['observed_at'] is None
    assert value['payload']=={'value':2} and value['raw_ref']==ref

def test_background_reference_never_extends_live_freshness(isolated,monkeypatch):
    now=2000000000000;monkeypatch.setattr(storage,'now_ms',lambda:now)
    storage.put('cmc','derivatives','p34',{'oi':'10'},now-900000,300000)
    value=storage.get('cmc','derivatives','p34')
    assert value['status']=='stale'
    assert value['policy']['reference_status']=='available'
    assert value['policy']['action_eligible'] is False
    assert storage.get('cmc','derivatives','p34',now+4000000)['policy']['reference_status']=='stale'

def test_queue_keeps_real_management_but_not_terminal_priority():
    assert signal_interval('paused',True,False)==30000
    assert signal_interval('expired',True,False)==30000
    assert signal_interval('paused',False,True) is None
    assert signal_interval('expired',False,True) is None
    assert signal_interval('trial',False,False) is None
    assert signal_interval('waiting',False,True)==60000

def test_chain_aliases_preserve_solana_case_and_reject_ambiguous_identities():
    assert contract_key('CT_501','AbCd')==('501','AbCd')
    assert contract_key('bsc','0xAbCd')==('56','0xabcd')
    inst=N(asset_id='wallet:S',spec={'chain_id':'CT_501','contract_address':'AbCd'})
    row={'id':99,'is_active':1,'platform':{'slug':'solana','token_address':'AbCd'}}
    assert exact_contract_links([inst],[row])[0]['external_id']=='99'
    assert exact_contract_links([inst],[row,{**row,'id':100}])==[]
    assert exact_contract_links([inst],[{**row,'platform':{'slug':'solana','token_address':'abcd'}}])==[]

def test_oi_notional_requires_aligned_price_but_quantity_survives():
    assert derivative_collection.aligned_notional('200','.01',100000,100000)=='2.00'
    assert derivative_collection.aligned_notional('200','.01',200000,100000) is None
    assert derivative_collection.aligned_notional('200','NaN',100000,100000) is None

def test_missing_paired_price_does_not_break_quantity_or_other_fields():
    from decimal import Decimal as D
    from engine.features.observations import paired_changes
    from engine.repository import merge_market_payload
    assert paired_changes(D('100'),D('2'),D('110'),None,'USDT')=={}
    merged=merge_market_payload({'oi_base':'100','oi_as_of':10000,'oi_status':'fresh'},
        {'oi_base':'110','oi_as_of':0,'oi_status':'source_time_unknown'})
    assert merged['oi_base']=='110' and merged['oi_as_of']==0 and merged['oi_status']=='source_time_unknown'

def test_derived_notional_retains_both_raw_inputs():
    from engine.adapters.base import Batch
    batch=Batch('binance');item={'base_quantity':'100','mark_price':'2','quote_asset':'USDT',
        'notional_quote':'200','event_time':100000,'received_at':100001,'raw_ref':'oi-original'}
    derivative_collection.notional_evidence(batch,item,'mark-original',99999)
    assert item['raw_ref']==batch.raw[0]['id']
    assert batch.raw[0]['payload']['oi_raw_ref']=='oi-original'
    assert batch.raw[0]['payload']['price_raw_ref']=='mark-original'

def test_shared_budget_fifo_prevents_slow_task_starvation(isolated,monkeypatch):
    from engine.pd2 import request_budget as budget
    from engine.models import UserPreference
    clock=[2000000000000];monkeypatch.setattr(budget,'now_ms',lambda:clock[0])
    monkeypatch.setattr(budget,'budget_spec',lambda *a:('p34.test',1,3,500))
    url='https://p34.test/api'
    assert budget.reserve(url,{},'live-first')==0
    assert budget.reserve(url,{},'history')>0
    clock[0]+=500
    assert budget.reserve(url,{},'new-live')>0
    assert budget.reserve(url,{},'history')==0
    clock[0]+=500
    assert budget.reserve(url,{},'new-live')==0
    assert budget.reserve(url,{},'over-budget')>0
    budget.cancel_ticket(url,'over-budget')
    with isolated() as s:
        row=s.get(UserPreference,'http_budget:p34.test')
        assert sum(v[1] for v in row.payload['entries'])==3 and row.payload['pending']==[]

def test_reference_credit_pacing_survives_new_calls_and_charges_actual_cost(isolated):
    now=2000000000000
    ok,next_at=storage.reference_turn('p34-test',300,now)
    assert ok and next_at>now
    assert storage.reference_turn('p34-test',300,now+1000)==(False,next_at)
    storage.reconcile_reference_cost('p34-test',2)
    allowed,due=storage.reference_turn('p34-test',300,next_at)
    assert not allowed and due==now+3*(next_at-now)
    assert storage.reference_turn('p34-test',300,due)[0]

def test_history_cursor_never_skips_gap_or_unclosed_record():
    from engine.pd2.history_backfill import advance,STEP
    start=1000*STEP;end=start+4*STEP
    rows=[{'open_time':start+i*STEP,'close_time':start+(i+1)*STEP,'is_closed':i!=2} for i in range(4)]
    assert advance(start,end,list(reversed(rows)))==start+2*STEP
    assert advance(start,end,rows[:1]+rows[2:])==start+STEP
    rows[2]['is_closed']=True
    assert advance(start+2*STEP,end,rows+rows)==end

def test_oi_history_ack_requires_complete_utc_grid():
    step=3600000;now=1000*step+120000
    pts=[{'event_time':t} for t in range(928*step,1001*step,step)]
    assert derivative_collection.coverage(pts,now)['complete']
    missing=derivative_collection.coverage(pts[:40]+pts[41:],now)
    assert not missing['complete'] and missing['missing']==[968*step]
    # A listed-today market must not require nonexistent pre-listing history.
    assert derivative_collection.coverage(pts[-1:],now,1000*step)['complete']

def test_hourly_funding_has_full_day_and_native_intervals():
    now=1000*3600000
    records=[{'fundingTime':now-i*3600000,'fundingRate':'.0001'} for i in range(26)]
    points=derivative_collection.funding_points(records,'x',now,'r')
    assert len(points)==26 and points[-1]['interval_seconds']==3600
    assert points[0]['settled_at']<=now-86400000

def test_fast_market_is_persisted_before_slow_market(monkeypatch):
    from engine.adapters.binance_universe import BinanceUniverseAdapter
    from engine.contracts import RuntimeConfig
    from engine.pd2 import collection_schedule
    monkeypatch.setattr(collection_schedule,'due',lambda *a,**kw:['FASTUSDT','SLOWUSDT'])
    monkeypatch.setattr('engine.adapters.binance_universe.stored_mark',lambda *a:None)
    async def scenario():
        adapter=BinanceUniverseAdapter();saved=[];persisted=asyncio.Event();slow=asyncio.Event();release=asyncio.Event();now=2000000000000
        adapter.selection=[{'symbol':n+'USDT','baseAsset':n,'quoteAsset':'USDT'} for n in ('FAST','SLOW')]
        adapter.selection_limit=RuntimeConfig().asset_limit;adapter.selected_at=now;adapter.volumes={'FASTUSDT':1,'SLOWUSDT':1}
        from engine.p39.timing import cycle
        adapter.volume_cycle=cycle(now)  # This fixture isolates OI, not the separate 5m volume refresh.
        monkeypatch.setattr('engine.adapters.binance_universe.now_ms',lambda:now)
        async def request(batch,method,url,**kwargs):
            if url.endswith('premiumIndex'):
                return [{'symbol':n+'USDT','markPrice':'10','time':now} for n in ('FAST','SLOW')],'mark',now
            assert url.endswith('openInterest')
            if kwargs['params']['symbol']=='SLOWUSDT':slow.set();await release.wait()
            return {'openInterest':'20','time':now},'oi',now
        async def persist(part):saved.extend(part.states);persisted.set()
        adapter.request=request
        try:
            task=asyncio.create_task(adapter.collect(RuntimeConfig(),on_batch=persist))
            await asyncio.wait_for(slow.wait(),2)
            await asyncio.wait_for(persisted.wait(),2)
            assert len(saved)==1 and saved[0]['instrument_id'].endswith('FASTUSDT')
            assert not task.done();release.set();result=await task
            assert len(saved)==2 and result.states==[] and result.metadata['persisted_states']==2
        finally:await adapter.close()
    asyncio.run(scenario())

def test_auxiliary_change_refreshes_snapshot_without_rewriting_trigger(isolated,monkeypatch):
    from copy import deepcopy
    from engine.models import UserPreference
    from engine.strategies import p33_runtime as runtime
    from test_p33_patterns import base,NOW
    # Fix the dependency hash to represent a newly received OI/capability fact;
    # closed candles and the original event must remain unchanged.
    dependency=['before']
    monkeypatch.setattr(runtime,'evidence_signature',lambda *a:(dependency[0],None))
    instrument=N(id='p34:test:evidence',spec={'tick_size':'.01'})
    with isolated.begin() as s:
        runtime.observe(s,instrument,base(),NOW);s.flush()
        old=deepcopy(s.get(UserPreference,'p33_shadow:'+instrument.id).payload)
        dependency[0]='after'
        runtime.observe(s,instrument,base(),NOW+31000);s.flush()
        current=s.get(UserPreference,'p33_shadow:'+instrument.id).payload
        assert current['evidence_signature']=='after'
        assert current['latest_snapshots']!=old['latest_snapshots']
        for event_id,event in old['active_events'].items():
            assert current['active_events'][event_id]['initial']==event['initial']
        # No repeated immutable snapshot for the same unchanged evidence.
        ids=deepcopy(current['latest_snapshots'])
        runtime.observe(s,instrument,base(),NOW+32000);s.flush()
        assert s.get(UserPreference,'p33_shadow:'+instrument.id).payload['latest_snapshots']==ids
