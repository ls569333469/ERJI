from copy import deepcopy
from engine.pd2.chart_contract import candidate_contract
from engine.pd2.derivatives import choose_reference
from engine.strategies.structures import VERSION


def test_reference_contract_does_not_disappear_when_fx_or_oi_is_missing():
    native={'instrument_id':'binance:perp:TUTUSDT','venue':'binance','fresh_oi':False,'fx_status':'missing_or_stale'}
    auxiliary={'instrument_id':'aster:perp:TUTUSDT','venue':'aster','fresh_oi':True,'fx_status':'available'}
    assert choose_reference([auxiliary,native],auxiliary['instrument_id'])==native
    assert choose_reference([]) is None


def test_pending_structure_has_boundaries_without_invented_buy_or_trigger():
    evidence={'instrument_id':'test','box':{'upper':'2','lower':'1','trigger_price':'2.1','start_time':100,'end_time':200,'rule_version':VERSION}}
    result=candidate_contract('event','test',evidence,'structure_pending',300,{'status':'verified','last_open_time':180})
    assert result['status']=='candidate'
    assert [a['kind'] for a in result['anchors']]==['box','trigger_boundary']
    assert result['entry_zone'] is result['protection'] is result['first_target'] is None
    assert result['anchors'][0]['last_open_time']==180
    assert candidate_contract('event','test',evidence,'structure_pending',300) is None
    assert candidate_contract('event','other',evidence,'structure_pending',300) is None
    assert candidate_contract('event','test',evidence,'invalidated',300) is None
    later=deepcopy(evidence);later['box']['end_time']=301
    assert candidate_contract('event','test',later,'structure_pending',300) is None
    assert candidate_contract('event','test',{**evidence,'trigger':{'close':'3'}},'structure_pending',300) is None


def test_aggregate_oi_time_belongs_to_contributing_markets_not_funding_reference():
    from engine.pd2.signal_projection import compact
    row={'id':'ep','asset_id':'TEST','instrument_id':'binance_spot:spot:TESTUSDT',
         'phase':'structure_pending','phase_label':'等待突破',
         'market':{'symbol':'TEST','venue':'binance_spot','quote_asset':'USDT','price':'100','as_of':1000},
         'action':{'code':'waiting','reason':'waiting'},'first_seen_at':1000,'first_seen_price':'100',
         'evidence':{},'derivatives':{
             'cross_market_oi':{'total_oi_usd':'300','markets':[{'observed_at':920},{'observed_at':980}]},
             'derivatives_reference_market':{'instrument_id':'binance:perp:TESTUSDT','observed_at':200},
         }}
    result=compact(row)
    assert result['oi_as_of']==920
    assert result['oi_usd']=='300'
