from copy import deepcopy
from engine.strategies.p33_replay import simulate
from test_p33_lifecycle import research_event
from test_p33_patterns import NOW,H


def candle(start=NOW,**changes):
    return {'open_time':start,'close_time':start+4*H,'is_closed':True,
            'open':'10.5','high':'12','low':'10','close':'11',**changes}


def test_simultaneous_stop_and_target_uses_conservative_cost_scenario():
    event=research_event()
    result=simulate(event,[candle(high='16',low='8')],24)
    assert result['exit_reason']=='stop_first_ambiguous'
    assert float(result['net_return_pct'])<0
    assert result['intrabar_exit_time'] is None and result['trading_enabled'] is False


def test_gap_stop_slippage_and_missing_history_are_not_optimistic_fills():
    event=research_event()
    result=simulate(event,[candle(),candle(NOW+4*H,open='7',low='6',close='8')],24)
    assert result['exit_reason']=='stop' and float(result['exit_price'])<7
    assert simulate(event,[candle(NOW+4*H)],24)['status']=='missing_entry_history'
    assert simulate(event,[candle(),candle(NOW+8*H)],24)['status']=='missing_exit_history'
    assert simulate(event,[candle(open='20',high='21')],24)['status']=='no_fill_in_entry_window'


def test_frozen_plan_and_horizon_censoring():
    event=research_event();original=deepcopy(event)
    rows=[candle(NOW+i*4*H) for i in range(6)]
    result=simulate(event,rows,24)
    assert result['exit_reason']=='time_exit' and result['exit_bar_close']==NOW+24*H
    assert simulate(event,rows,72)['status']=='right_censored_history'
    assert event==original
    event['plan']['targets']['t1']=None
    assert simulate(event,rows,24)['status']=='missing_target'


def test_cost_gate_excludes_low_reward_without_using_later_price_gain():
    event=research_event();event['plan']['targets']['t1']['low']='11'
    assert simulate(event,[candle(high='100')],24)['status']=='insufficient_net_r'
