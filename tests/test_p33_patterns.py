"""Causal replay fixtures: actual OHLCV math, no mocked indicator values."""
from copy import deepcopy
import math
import numpy as np
import talib
from engine.strategies.p33 import evaluate, Facts, recognize, NAMES, VERSION

H=3600000
NOW=2000*24*H


def bars(prices,tf='240',end=NOW):
    step={'60':H,'240':4*H,'1D':24*H,'1W':168*H}[tf]
    return [{'open_time':end-(len(prices)-i)*step,'close_time':end-(len(prices)-i-1)*step,
             'received_at':end-(len(prices)-i-1)*step,'is_closed':True,
             'open':str(p-.1),'high':str(p+.3),'low':str(p-.3),'close':str(p),
             'base_volume':'100','quote_volume':'1000'} for i,p in enumerate(prices)]


def base():
    four=bars([10+math.sin(i)*.3 for i in range(60)])
    four[-1].update(open='10',low='9.9',high='13',close='12.8',quote_volume='2500')
    days=bars([11+math.sin(i)*.2 for i in range(45)],'1D',NOW-24*H)
    return {'240':four,'1D':days}


def test_recent_high_excludes_trigger_and_future_and_has_real_anchors():
    data=base();result=evaluate(data,NOW,'.01','test')
    hit=next(r for r in result['results'] if r['strategy_id']=='recent_high')
    assert hit['status']=='triggered' and hit['trading_enabled'] is False
    assert hit['anchors'][-1]['open_time']==data['240'][-1]['open_time']
    changed=deepcopy(data)
    future=deepcopy(changed['240'][-1]);future.update(open_time=NOW,close_time=NOW+4*H,received_at=NOW+4*H,high='99999')
    changed['240'].append(future)
    assert evaluate(changed,NOW,'.01','test')==result
    changed['240'][-2]['is_closed']=False
    assert not any(r['status']=='triggered' for r in evaluate(changed,NOW,'.01','test')['results'])


def test_missing_quote_volume_gaps_or_late_received_are_not_buy_signals():
    for change in ('volume','gap','late'):
        data=base()
        if change=='volume':data['240'][-1]['quote_volume']=None
        elif change=='gap':data['240'].pop(-10)
        else:data['240'][-1]['received_at']=NOW+1
        hit=recognize('recent_high',Facts(data,NOW,'.01'),'test')
        assert hit['status']!='triggered'


def test_ath_requires_same_market_history_proof_and_beats_recent_high():
    data=base();facts=Facts(data,NOW,'.01')
    assert recognize('ath',facts,'test')['status']=='missing_data'
    coverage={'complete':True,'market_id':'test','as_of':NOW-4*H,'end_time':NOW-4*H,
              'start_time':NOW-1000*24*H,'high':'12','scope':'contract_since_listing'}
    result=evaluate(data,NOW,'.01','test',coverage)
    assert result['main']['strategy_id']=='ath'
    assert result['main']['label']=='合约历史新高突破'
    assert recognize('ath',facts,'other',coverage)['status']=='missing_data'


def test_squeeze_needs_prior_low_bandwidth_then_expansion_and_volume():
    prices=[10+math.sin(i)*2 for i in range(145)]+[10+math.sin(i)*.01 for i in range(25)]+[12]
    four=bars(prices);four[-1]['quote_volume']='3000'
    result=recognize('squeeze',Facts({'240':four},NOW,'.001'),'test')
    assert result['status']=='triggered' and result['required_indicators']==['bb']
    four[-1]['quote_volume']='100'
    assert recognize('squeeze',Facts({'240':four},NOW,'.001'),'test')['status']=='waiting'


def test_double_bottom_draws_two_real_lows_not_a_box():
    prices=[11+math.sin(i)*.1 for i in range(40)]+[10,9,8,9,10,12,11,10,9,8,9,10,11,13]
    four=bars(prices);four[-1]['quote_volume']='3000'
    result=recognize('double_bottom',Facts({'240':four},NOW,'.001'),'test')
    assert result['status']=='triggered'
    assert len([a for a in result['anchors'] if a['kind']=='pivot'])==2
    assert not any(a['kind']=='box' for a in result['anchors'])
    four[-5]['low']='5'
    assert recognize('double_bottom',Facts({'240':four},NOW,'.001'),'test')['status']!='triggered'


def test_retest_requires_parent_then_actual_touch_and_reclaim():
    one=bars([10.1]*56+[10.02,10.1,10.8],'60')
    one[-1]['quote_volume']='3000'
    parent={'market_id':'test','status':'triggered','trigger':{'close_time':NOW-4*H},
            'boundary':'10','atr':'1','event_id':'parent'}
    result=recognize('retest',Facts({'60':one},NOW,'.001'),'test',parent=parent)
    assert result['status']=='triggered' and result['parent_event_id']=='parent'
    assert recognize('retest',Facts({'60':one},NOW,'.001'),'test')['status']=='missing_data'
    parent['trigger']['close_time']=NOW-30*H
    assert recognize('retest',Facts({'60':one},NOW,'.001'),'test',parent=parent)['status']=='expired'


def test_all_rules_fail_closed_without_data_and_have_frozen_version():
    result=evaluate({},NOW,'.01','test')
    assert len(result['results'])==len(NAMES)==9
    assert all(r['status']=='missing_data' and r['strategy_version']==VERSION for r in result['results'])
    assert result['main'] is None and result['trading_enabled'] is False


def test_closed_macd_cross_also_needs_price_break_and_higher_four_hour_lows():
    one=bars([20-i*.05 for i in range(65)]+[23],'60')
    one[-1]['quote_volume']='3000'
    four=bars([15+i*.05+math.sin(i) for i in range(70)],end=NOW-4*H)
    result=recognize('early_strength',Facts({'60':one,'240':four},NOW,'.001'),'test')
    # A steadily falling series has no swing high, so a MACD cross alone cannot trigger.
    assert result['status']!='triggered'
    one=bars([20-i*.05+math.sin(i)*.15 for i in range(62)]+[23],'60')
    one[-1]['quote_volume']='3000'
    result=recognize('early_strength',Facts({'60':one,'240':four},NOW,'.001'),'test')
    assert result['status']=='triggered' and result['required_indicators']==['macd']
    one[-1]['is_closed']=False
    assert recognize('early_strength',Facts({'60':one,'240':four},NOW,'.001'),'test')['status']!='triggered'


def test_trend_pullback_requires_upward_daily_structure():
    daily=bars([80+i*.2+math.sin(i) for i in range(90)],'1D',NOW-24*H)
    four=bars([100+i*.05+math.sin(i)*.6 for i in range(80)],end=NOW-4*H)
    ema=float(talib.EMA(np.array([float(b['close']) for b in four]),20)[-2])
    four[-1].update(low=str(ema),close=str(ema+.2),high=str(ema+.4))
    one=bars([102+math.sin(i)*.1 for i in range(60)]+[108],'60');one[-1]['quote_volume']='3000'
    data={'60':one,'240':four,'1D':daily}
    result=recognize('trend_pullback',Facts(data,NOW,'.001'),'test')
    assert result['status']=='triggered' and result['required_indicators']==['ema']
    data['1D']=bars([100-i*.2+math.sin(i) for i in range(90)],'1D',NOW-24*H)
    assert recognize('trend_pullback',Facts(data,NOW,'.001'),'test')['status']!='triggered'


def test_failed_breakdown_needs_preexisting_support_and_followthrough():
    four=bars([10+math.sin(i)*.1 for i in range(60)],end=NOW-4*H)
    four[-4]['low']='8'
    four[-1].update(low='7',close='9',high='10')
    one=bars([9+math.sin(i)*.1 for i in range(60)]+[11],'60');one[-1]['quote_volume']='3000'
    result=recognize('failed_breakdown',Facts({'60':one,'240':four},NOW,'.001'),'test')
    assert result['status']=='triggered'
    assert result['anchors'][0]['price']=='8'
    four[-1]['close']='7.5'
    assert recognize('failed_breakdown',Facts({'60':one,'240':four},NOW,'.001'),'test')['status']!='triggered'


def test_multiple_attempts_keep_45_day_high_not_lower_local_high():
    prices=[18+math.sin(i)*.1 for i in range(270)]+[21.5]
    four=bars(prices);four[230]['high']='20.3';four[250]['high']='20.3';four[-1]['quote_volume']='3000'
    daily=bars([20]*45,'1D',NOW-24*H)
    data={'240':four,'1D':daily}
    result=recognize('repeated_test',Facts(data,NOW,'.001'),'test')
    assert result['status']=='triggered' and result['boundary']=='20.3'
    daily[0]['high']='25'
    assert recognize('repeated_test',Facts(data,NOW,'.001'),'test')['status']!='triggered'
