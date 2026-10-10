from decimal import Decimal as D
from engine.pd2.analysis import level_role
from engine.pd2.portfolio import risk_summary,plan_risk

def bar(t,c,h,l):return {'close_time':t,'close':str(c),'high':str(h),'low':str(l)}
def test_break_does_not_immediately_become_support():
    b=[bar(1,12,13,9)]
    assert level_role('resistance',D(9),D(11),b)['status']=='role_pending'
    state=level_role('resistance',D(9),D(11),b+[bar(2,12,13,10)])
    assert state['kind']=='support' and state['events'][-1]['known_at']==2
    assert level_role('resistance',D(9),D(11),b+[bar(2,8,10,7)])['status']=='invalidated'

def test_costs_unknown_are_not_zero_and_risk_includes_fees():
    p={'id':'p','instrument_id':'test','entry':'10','stop':'9','quantity':'2','state':'open'}
    assert risk_summary(1000,1,5,[p],{'test':1})['status']=='incomplete'
    r=risk_summary(1000,1,5,[p],{'test':1},{'test':{'fee_rate':'.001','slippage_rate':'.002','funding_rate':'.003'}})
    assert D(r['open_risk_usd'])==D('2.138') and r['includes_fees']
    assert risk_summary(0,1,5,[],{})['max_new_plan_risk_usd'] is None

def test_size_never_exceeds_depth_test_or_account_risk():
    r={'status':'available','max_new_plan_risk_usd':'10'}
    e={'status':'eligible','quote_usd_rate':'1','stop_path_cost':'.1','base_quantity':'3'}
    s=plan_risk(r,'10','9',e)
    assert s['quantity']=='3' and D(s['max_loss_scenario_usd'])==D('3.3')
    assert plan_risk({**r,'max_new_plan_risk_usd':'0'},'10','9',e)['status']=='risk_exhausted'
