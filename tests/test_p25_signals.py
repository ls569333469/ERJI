import json,sys,time
from types import SimpleNamespace as N
from decimal import Decimal as D
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from engine.db import engine
from engine.models import Asset,Instrument,MarketState,CurrentSignal
from engine.pd2 import signal_projection as projection
from engine.pd2.universe_policy import resolve,primary_eligible
from engine.pd2.collection_schedule import choose_due
from engine.universe import select_binance_universe

@pytest.fixture
def isolated(monkeypatch):
    conn=engine.connect();tx=conn.begin()
    factory=sessionmaker(bind=conn,expire_on_commit=False,join_transaction_mode='create_savepoint')
    for name,module in list(sys.modules.items()):
        if name.startswith('engine.') and hasattr(module,'Session'):monkeypatch.setattr(module,'Session',factory)
    try:yield factory
    finally:tx.rollback();conn.close()

def inst(asset,venue='binance',**spec):
    return N(asset_id=asset,venue=venue,spec=spec,quote_asset='USDT',market_type='linear_perpetual')

def test_auxiliary_catalog_never_admits_unlisted_assets():
    aliases,admitted=resolve([inst('AAA'),inst('aster:AAA','aster'),inst('ONLY','hyperliquid')],[])
    assert admitted=={'AAA'} and aliases['aster:AAA']=='aster:AAA'
    aliases,admitted=resolve([inst('AAA'),inst('aster:AAA','aster')],[N(provider='cmc',status='verified',external_id='5',asset_id=a) for a in ('AAA','aster:AAA')])
    assert aliases['aster:AAA']=='AAA' and len(admitted)==1

def test_alpha_no_fdv_substitution_and_wallet_quality():
    assert not primary_eligible(inst('CA','binance_alpha',fdv=100000000))
    assert primary_eligible(inst('CA','wallet_rising',circulating_cap_usd='2000000',liquidity_usd='100000'))
    assert not primary_eligible(inst('USDC'))

def test_full_asset_catalog_retains_usdc_and_quiet_assets():
    catalog=[{'symbol':f'A{i}USDT','baseAsset':f'A{i}','quoteAsset':'USDT','contractType':'PERPETUAL','status':'TRADING'} for i in range(650)]
    catalog.append({**catalog[0],'symbol':'A0USDC','quoteAsset':'USDC'})
    selected,count,_=select_binance_universe(catalog,[],100000)
    assert count==650 and len(selected)==651

def test_due_intervals_and_ordinary_fairness():
    assert choose_due(['plain','candidate','buy'],{'plain':0,'candidate':0,'buy':0},{'plain':300000,'candidate':60000,'buy':30000},30000)==['buy']
    assert set(choose_due(['plain','candidate','buy'],{'plain':0,'candidate':0,'buy':0},{'plain':300000,'candidate':60000,'buy':30000},300000))=={'plain','candidate','buy'}
    assert len(choose_due([str(i) for i in range(650)],{}, {},1000000))==20

NOW=2000000000000

def seed(factory,count=1,action='trial'):
    with factory.begin() as s:
        for i in range(count):
            asset=f'P25TEST{i}';iid='binance:perp:'+asset+'USDT';key='p25plan'+str(i)
            s.add(Asset(id=asset,symbol=asset,known_at=NOW));s.flush()
            s.add(Instrument(id=iid,asset_id=asset,venue='binance',native_symbol=asset+'USDT',market_type='linear_perpetual',quote_asset='USDT',spec={}));s.flush()
            s.add(MarketState(instrument_id=iid,as_of=NOW,payload={'price':'1','price_kind':'last'}))
            s.add(CurrentSignal(id=key,asset_id=asset,instrument_id=iid,action=action,first_seen_at=NOW-i*1000,computed_at=NOW,valid_until=NOW+10000,payload={
             'id':'ep'+str(i),'asset_id':asset,'instrument_id':iid,'symbol':asset,'venue':'binance','quote_asset':'USDT','price':'1','action_code':action,'entry_type':'小仓买入',
             'reason':'结构与成交触发，盘口及成本满足','entry_zone':['.98','1.02'],'protection':'.90','phase_label':'结构触发','box':{'upper':'.97','lower':'.80'},
             'first_seen_at':NOW-i*1000,'first_seen_price':'.95','since_first_pct':'5.263','targets':{'t1':{'low':'1.2','high':'1.22','remaining_upside_pct':'20'},'daily':None,'weekly':None},
             'oi_usd':'123456','oi_cap_ratio':'.12','oi_scope':'已覆盖市场合计','oi_changes':[],'valid_until':NOW+10000}))

@pytest.mark.parametrize('change',[{'valid_until':NOW-1},{'price':'1.10'},{'price':'.97'},{'price':'.89'},{'as_of':NOW-15001}])
def test_buy_removed_when_quote_or_plan_becomes_unsafe(isolated,monkeypatch,change):
    monkeypatch.setattr(projection,'now_ms',lambda:NOW);seed(isolated)
    assert projection.read('buy')['pagination']['total']==1
    with isolated.begin() as s:
        if 'valid_until' in change:s.get(CurrentSignal,'p25plan0').valid_until=change['valid_until']
        else:
            m=s.get(MarketState,'binance:perp:P25TEST0USDT')
            if 'price' in change:m.payload={**m.payload,'price':change['price']}
            else:m.as_of=change['as_of']
    assert projection.read('buy')['rows']==[]
    row=projection.read('risk')['rows'][0]
    assert row['action_code']=='paused' and row['entry_type'] is None

def test_stable_order_does_not_freeze_safety_and_history_keeps_episodes(isolated,monkeypatch):
    monkeypatch.setattr(projection,'now_ms',lambda:NOW);seed(isolated,2)
    first=projection.read('buy',size=1);token=first['pagination']['list_snapshot']
    with isolated.begin() as s:
        row=s.get(CurrentSignal,'p25plan0');row.valid_until=NOW-1
        old=s.get(CurrentSignal,'p25plan1')
        s.add(CurrentSignal(id='older',asset_id=old.asset_id,instrument_id=old.instrument_id,action='ended',first_seen_at=NOW-100000,computed_at=NOW,valid_until=NOW-1,payload={**old.payload,'action_code':'ended'}))
    next_=projection.read('buy',size=1,snapshot_id=token)
    assert next_['rows'][0]['symbol']=='P25TEST1'
    assert projection.read('history')['pagination']['total']==1

def test_compact_page_payload_budget_and_read_timing(isolated,monkeypatch):
    monkeypatch.setattr(projection,'now_ms',lambda:NOW);seed(isolated,100)
    t=time.perf_counter();result=projection.read('buy');elapsed=time.perf_counter()-t
    encoded=json.dumps(result,ensure_ascii=False).encode()
    assert len(result['rows'])==25 and len(encoded)<=50000
    assert all('candles_at_snapshot' not in r for r in result['rows'])
    print(f'P25 synthetic 100-signal page: {len(encoded)} bytes, {elapsed:.3f}s')

def test_waiting_candidate_not_subject_to_execution_quote_freshness(isolated,monkeypatch):
    monkeypatch.setattr(projection,'now_ms',lambda:NOW);seed(isolated,action='waiting')
    with isolated.begin() as s:s.get(MarketState,'binance:perp:P25TEST0USDT').as_of=NOW-60000
    assert projection.read('waiting')['pagination']['total']==1


def test_price_stream_does_not_discard_new_oi_or_accept_old_oi():
    from engine.repository import merge_market_payload
    old={'as_of':100,'price':'2','price_kind':'last','oi_as_of':70,'oi_base':'10'}
    merged=merge_market_payload(old,{'as_of':90,'price':'1.9','oi_as_of':95,'oi_base':'12'})
    assert merged['price']=='2' and merged['as_of']==100 and merged['oi_base']=='12'
    merged=merge_market_payload(merged,{'as_of':110,'price':'2.1','oi_as_of':80,'oi_base':'9'})
    assert merged['price']=='2.1' and merged['oi_base']=='12'

def test_cross_process_postgres_slot_cap():
    from engine.pd2.request_budget import acquire_slot,release_slot
    slots=[]
    try:
        for _ in range(4):
            lease=acquire_slot();assert lease is not None;slots.append(lease)
        assert acquire_slot() is None
    finally:
        for lease in slots:release_slot(lease)
    lease=acquire_slot();assert lease is not None;release_slot(lease)

def test_avwap_is_available_only_after_pivot_confirmation_and_uses_base_volume():
    from engine.pd2.analysis import technical
    highs=[10,11,15,12,11,10,11,12]
    rows=[dict(open_time=i*3600000,close_time=(i+1)*3600000,received_at=(i+1)*3600000,is_closed=True,open=str(h-1),high=str(h),low=str(h-2),close=str(h-1),base_volume=str(i+1),quote_volume='1000') for i,h in enumerate(highs)]
    result=technical(rows,'60',8*3600000)
    series=result['series']
    assert series[3]['avwap_high'] is None
    expected=sum(D(highs[i]-1)*D(i+1) for i in range(2,5))/sum(D(i+1) for i in range(2,5))
    assert D(series[4]['avwap_high'])==expected
    shorter=technical(rows[:5],'60',5*3600000)
    assert shorter['series'][4]['avwap_high']==series[4]['avwap_high']
    for r in rows:r['base_volume']=None
    assert all(r['avwap_high'] is None for r in technical(rows,'60',8*3600000)['series'])

def test_technical_cache_never_returns_fresh_after_closed_bars_expire():
    from engine.pd2.analysis import technical
    rows=[dict(open_time=i*3600000,close_time=(i+1)*3600000,received_at=(i+1)*3600000,is_closed=True,open=str(100+i),high=str(102+i),low=str(99+i),close=str(101+i),base_volume='5',quote_volume='100') for i in range(70)]
    result=technical(rows,'60',70*3600000);assert result['status']=='fresh'
    result['series'].clear()
    next_=technical(rows,'60',72*3600000)
    assert next_['status']=='stale' and len(next_['series'])==70


def test_disqualified_primary_keeps_original_plan_as_risk(isolated,monkeypatch):
    monkeypatch.setattr(projection,'now_ms',lambda:NOW);seed(isolated)
    with isolated.begin() as s:
        i=s.get(Instrument,'binance:perp:P25TEST0USDT');i.spec={'status':'delisted'}
    row={'id':'ep0','plan_id':'p25plan0','asset_id':'P25TEST0','instrument_id':'binance:perp:P25TEST0USDT',
         'market':{'venue':'binance','quote_asset':'USDT','price':'1'},'evidence':{'evaluated_at':NOW},
         'action':{'code':'trial','reason':'buy'},'phase':'early_trigger','phase_label':'早期触发',
         'first_seen_at':NOW-5000,'first_seen_price':'.9'}
    projection.publish([row])
    assert projection.read('buy')['rows']==[]
    risk=projection.read('risk')['rows'][0]
    assert risk['first_seen_at']==NOW-5000 and '主池资格' in risk['reason']


def test_alpha_cap_crosscheck_rejects_fdv_and_disagreement():
    from engine.adapters.expanded_markets import alpha_quality
    token={'price':'2','circulatingSupply':'1000000','marketCap':'2000000','fdv':'900000000','liquidity':'120000'}
    assert alpha_quality(token)['circulating_cap_usd']=='2000000'
    assert 'circulating_cap_usd' not in alpha_quality({**token,'marketCap':'900000000'})
    assert 'circulating_cap_usd' not in alpha_quality({'fdv':'100000000','liquidity':'120000'})


def test_chandelier_standard_reference_has_warmup_and_known_value():
    from engine.pd2.analysis import technical
    rows=[dict(open_time=i*3600000,close_time=(i+1)*3600000,received_at=(i+1)*3600000,is_closed=True,open='10',high='12',low='8',close='10',base_volume='5',quote_volume='50') for i in range(60)]
    series=technical(rows,'60',60*3600000)['series']
    assert series[20]['chandelier_reference'] is None
    # Constant true range=4; 22-bar high=12; reference=12-3*4=0.
    assert D(series[21]['chandelier_reference'])==0


def test_account_budget_includes_external_usage_and_preserves_ten_percent(isolated,monkeypatch):
    from engine.pd2 import storage
    from engine.contracts import RuntimeConfig
    monkeypatch.setattr(storage,'now_ms',lambda:NOW)
    cfg=RuntimeConfig().sources.cmc
    storage.account_usage('cmc',{'data':{'plan':{'credit_limit_monthly':100},'usage':{'current_month':{'credits_used':90,'credits_left':10}}}},NOW)
    allowed,reason=storage.reserve('cmc',cfg,priority='critical',now=NOW)
    assert not allowed and reason=='budget_exhausted'
    report=next(r for r in storage.budget_status(RuntimeConfig()) if r['provider']=='cmc')
    assert report['effective_used']>=90 and report['usage_scope']=='account_snapshot_plus_local_delta'


def test_thousand_signal_projection_latency_sample(isolated,monkeypatch):
    monkeypatch.setattr(projection,'now_ms',lambda:NOW);seed(isolated,1000)
    samples=[];token=None
    for _ in range(20):
        started=time.perf_counter();result=projection.read('buy',snapshot_id=token);samples.append(time.perf_counter()-started)
        token=result['pagination']['list_snapshot']
    assert result['pagination']['total']>=1000 and len(result['rows'])==25
    size=len(json.dumps(result,ensure_ascii=False).encode());assert size<=50000
    print(f'P25 local synthetic 1000 signals: page bytes={size}, 20 serial reads P95={sorted(samples)[18]:.4f}s; not production or concurrent load')
