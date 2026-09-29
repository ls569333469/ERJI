from decimal import Decimal as D
import numpy as np
import talib
from engine.pd2.analysis import weekly_cross_gate,aggregate,choose_targets
from engine.pd2.catalog import map_symbols
from engine.pd2.native_streams import force_event,trade_event
from engine.pd2.derivatives import cmc_liquidations
from engine.pd2.auth import create_session,verify_session
from engine.adapters.expanded_markets import contract_asset

def test_weekly_threshold_matches_independent_next_close_recurrence():
    closes=150-np.arange(80,dtype=float)**2*.01
    bars=[dict(open_time=i*604800000,close_time=(i+1)*604800000,is_closed=True,close=str(c)) for i,c in enumerate(closes)]
    gate=weekly_cross_gate(bars,bars[-1]['close_time'])
    boundary=float(gate['price'])
    assert gate['status']=='conditional_close'
    assert abs(talib.MACD(np.append(closes,boundary),12,26,9)[2][-1])<1e-10
    assert talib.MACD(np.append(closes,boundary*1.001),12,26,9)[2][-1]>0
    assert talib.MACD(np.append(closes,boundary*.999),12,26,9)[2][-1]<0

def test_liquidation_fill_side_and_dedup_identity():
    d={'o':{'s':'BTCUSDT','T':100000,'S':'BUY','l':'2','q':'100','z':'15','ap':'50','p':'51'}}
    x=force_event('binance',d)
    assert x['liquidated_side']=='short' and x['notional_quote']=='100'
    assert force_event('binance',d)['id']==x['id']
    assert force_event('aster',d)['id']!=x['id']
    assert force_event('binance',{'o':{**d['o'],'l':'0'}}) is None

def test_trade_side_is_aggressor_not_maker():
    x=trade_event('binance',{'a':123,'s':'BTCUSDT','T':123,'p':'10','q':'2','m':True})
    assert x['aggressor_side']=='sell' and x['notional_quote']=='20'

def test_liquidation_zero_is_not_missing_and_identity_is_not_ticker():
    d={'data':{'cryptocurrencies':[{'symbol':'SAME','crypto_id':12,'quotes':[{'symbol':'USD','last_updated':2000000000,'short_liquidations_1h':0,'long_liquidations_1h':None}]}]}}
    out=cmc_liquidations(d,12,2000000000000)
    assert out['windows'][0]['short_usd']=='0' and out['windows'][0]['long_usd'] is None
    assert cmc_liquidations(d,13,2000000000000)['status']=='not_returned'

def test_no_activated_projection_before_confirmed_box():
    box={'upper':'2','lower':'1','end_time':20}
    assert choose_targets([],2.1,box)['t1'] is None
    assert choose_targets([],2.1,box,box_confirmed=True)['t1']['low']=='3'

def test_pool_identity_preserves_solana_case_and_evm_chain():
    assert contract_asset('CT_501','AbC')!='CT_501:abc'
    assert contract_asset('56','0xAbC')=='56:0xabc'
    assert contract_asset('1','0xAbC')!='56:0xabc'

def test_session_tamper_and_expiration():
    cookie,_=create_session('a'*32,100)
    assert verify_session(cookie,'a'*32,101)
    assert not verify_session(cookie+'x','a'*32,101)
    assert not verify_session(cookie,'b'*32,101)
    assert not verify_session(cookie,'a'*32,1000000)

def test_map_catalog_data_only():
    import json
    text=json.dumps([1,'1:{"symbols":["BTC","1000PEPE"]}'])
    page='<script>self.__next_f.push('+text+')</script>'
    assert map_symbols(page)==['BTC','1000PEPE']

def test_aggregate_unknown_base_volume_not_zero():
    bars=[dict(open_time=i*3600000,close_time=(i+1)*3600000,is_closed=True,open='1',high='2',low='1',close='2',base_volume=None,quote_volume='10') for i in range(2)]
    row=aggregate(bars,'120',7200000)[0]
    assert row['base_volume'] is None and row['quote_volume']=='20'
