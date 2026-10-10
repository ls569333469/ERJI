from copy import deepcopy
from decimal import Decimal as D
from engine.pd2.config import ExecutionConfig
from engine.strategies.p33 import evaluate
from engine.strategies.p33_lifecycle import identity, group_hits, freeze_plan, qualify, follow
from test_p33_patterns import base, bars, NOW, H


def hit():
    return evaluate(base(), NOW, '.01', 'binance:perp:TESTUSDT')['main']


def test_same_boundary_merges_templates_but_other_markets_and_structures_do_not():
    a=hit();b=deepcopy(a);b.update(strategy_id='ath',event_id='ath-1')
    b['boundary']=str(D(a['boundary']))+'0'
    assert identity(a)==identity(b)
    groups=group_hits([a,b])
    assert len(groups)==1 and next(iter(groups.values()))[0]['strategy_id']=='ath'
    for key,value in [('market_id','other'),('boundary','99'),('parent_event_id','parent'),('config_hash','next')]:
        other={**b,key:value}
        assert identity(a)!=identity(other)
    later=deepcopy(b);later['trigger']['close_time']+=4*H
    assert len(group_hits([a,later]))==2


def test_plan_has_confirmed_targets_with_causal_atr_and_never_invents_ath_ceiling():
    data=base();event=hit();event['entry_zone']=['99','100'];plan=freeze_plan(event,data,NOW,'.01')
    assert plan['targets']['t1'] is None
    event=hit()
    data['1D']=bars([18]*10+[22]+[18]*44,'1D',NOW)
    plan=freeze_plan(event,data,NOW,'.01')
    target=plan['targets']['t1']
    assert target and target['timeframe']=='1D'
    assert target['known_at']==NOW and target['anchor']['known_at']<NOW
    assert D(target['low'])>D(plan['entry_zone'][1])
    future=deepcopy(data['1D'][-1]);future.update(open_time=NOW,close_time=NOW+24*H,received_at=NOW+24*H,high='999')
    data['1D'].append(future)
    assert freeze_plan(event,data,NOW,'.01')==plan


def research_event():
    value=hit();value.update(entry_zone=['10','11'],protection='9',expires_at=NOW+4*H)
    value['invalidation']={'timeframe':'240','price':'9.5','condition':'close_below'}
    value['timeframe']='240'
    return {'initial':value,'status':'triggered','plan':{'as_of':NOW,'entry_zone':['10','11'],
        'original_protection':'9','expires_at':NOW+4*H,'targets':{
        't1':{'low':'15','known_at':NOW},'daily':None,'weekly':None}}}


def test_cost_eligibility_uses_native_book_and_does_not_release_a_buy():
    event=research_event();config=ExecutionConfig()
    market={'instrument_id':event['initial']['market_id'],'quote_asset':'USDT','quote_usd_rate':'1'}
    book={'instrument_id':market['instrument_id'],'scope':'execution_market',
          'received_at':NOW,'observed_at':NOW,'asks':[['10','10000']],'bids':[['9.99','10000']]}
    result=qualify(event,market,book,config,NOW)
    assert result['rule_action']['code']=='participate'
    assert result['execution']['status']=='eligible'
    assert result['trading_enabled'] is False and result['release_status']=='technical_analysis'
    book['instrument_id']='binance:perp:OTHERUSDT'
    assert qualify(event,market,book,config,NOW)['rule_action']['code']=='paused'
    book['instrument_id']=market['instrument_id'];book['observed_at']=NOW-20000
    assert qualify(event,market,book,config,NOW)['rule_action']['code']=='paused'
    book['observed_at']=NOW;market.pop('quote_usd_rate')
    assert qualify(event,market,book,config,NOW)['execution']['status']=='unavailable'


def test_target_first_observation_survives_pullback_and_stop_target_order_is_unknown():
    event=research_event()
    bar={'open_time':NOW,'close_time':NOW+4*H,'received_at':NOW+4*H,'is_closed':True,'low':'9.8','high':'16','close':'14'}
    result=follow(event,{'240':[bar]},NOW+4*H)
    first=deepcopy(result['target_first_observed'])
    later={**bar,'open_time':NOW+4*H,'close_time':NOW+8*H,'received_at':NOW+8*H,'high':'13','close':'12'}
    assert follow(result,{'240':[bar,later]},NOW+8*H)['target_first_observed']==first
    bar['low']='8'
    result=follow(event,{'240':[bar]},NOW+4*H)
    assert result['target_first_observed']['t1']['intrabar_order']=='unknown_stop_and_target'
    assert result['status']=='protection_touched'
    assert follow(result,{'240':[later]},NOW+8*H)==result


def test_preplan_high_is_not_counted_and_checked_through_stops_at_invalidation():
    event=research_event();event['plan']['as_of']=NOW+1
    bar={'open_time':NOW,'close_time':NOW+4*H,'received_at':NOW+4*H,'is_closed':True,'low':'9.6','high':'16','close':'10'}
    result=follow(event,{'240':[bar]},NOW+4*H)
    assert result['target_first_observed']=={}
    bar['close']='9.4'
    later={**bar,'open_time':NOW+4*H,'close_time':NOW+8*H,'received_at':NOW+8*H}
    result=follow(event,{'240':[bar,later]},NOW+8*H)
    assert result['checked_through']==NOW+4*H
    assert result['invalidation_close_time']==NOW+4*H


def test_missing_candle_interval_is_explicit_and_stale_series_blocks_new_entry():
    event=research_event()
    bar={'open_time':NOW+4*H,'close_time':NOW+8*H,'received_at':NOW+8*H,'is_closed':True,'low':'10','high':'11','close':'10.5'}
    result=follow(event,{'240':[bar]},NOW+8*H)
    assert result['coverage_gap_first_observed_at']==NOW+8*H
    result=qualify(event,None,None,ExecutionConfig(),NOW+8*H)
    assert 'candles_stale' in result['rule_action']['reason_codes']
