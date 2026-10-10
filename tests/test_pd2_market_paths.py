from decimal import Decimal as D
from engine.pd2.oi_aggregate import aggregate_oi,compare_snapshots
from engine.pd2.market_selection import compare_books
from engine.pd2.opportunity import price_distance
from engine.pd2.derivatives import model_map

def test_native_replaces_vendor_and_coverage_change_is_not_inflow():
 n=[{'instrument_id':'binance:BTC','venue':'binance','native_pair':'BTC/USDT','notional_usd':'110','observed_at':1000}]
 v={'markets':[{'market_key':'270:1','venue':'binance','native_pair':'BTC/USDT','notional_usd':'100'}, {'market_key':'okx:2','venue':'okx','native_pair':'BTC/USDT','notional_usd':'200'}]}
 r=aggregate_oi(n,v,1000);assert r['total_oi_usd']=='310' and len(r['markets'])==2
 changed=aggregate_oi(n,{},1000);assert compare_snapshots(r,changed)['status']=='coverage_changed'
 assert price_distance(1,4)=={'price_multiple':'4','remaining_upside_pct':'300','drawdown_pct':'-75.00'}

def test_liquidation_distance_uses_model_reference_not_other_market_mark():
 d={'symbol':'A','exchange_symbol':'AUSDT','as_of':1700000000000,'real_levels':{'reference_price':100,'levels':[{'price':110,'side':'short'}]}}
 r=model_map(d,'A','AUSDT',200,1700000000000)
 assert r['levels'][0]['distance_pct']=='10.0'
 d['real_levels'].pop('reference_price');assert model_map(d,'A','AUSDT',200,1700000000000)['levels'][0]['distance_pct'] is None

def test_execution_market_chosen_by_same_usd_amount_not_oi_or_volume():
 def b(i,ask,bid,fx='1'):
  return {'instrument_id':i,'quote_usd_rate':fx,'fee_rate':'.0005','book':{'asks':[[ask,'10000']],'bids':[[bid,'10000']],'scope':'execution_market','observed_at':1000,'received_at':1000}}
 r=compare_books([b('tight','1','.999'),b('wide','1','.99'),b('unknown','1','1',None)],1000,1000)
 assert [x['instrument_id'] for x in r['ranked']]==['tight','wide'];assert r['excluded'][0]['reason']=='fx_missing'
