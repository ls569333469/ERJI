"""User-declared research positions; never account balances or executable orders."""
from decimal import Decimal as D
from engine.db import Session
from engine.models import UserPreference,Instrument
from engine.pd2.providers import quote_usd_rate
from engine.adapters.base import now_ms

def risk_summary(account_usd,per_plan_pct,total_pct,positions,fx,costs=None):
    account=D(str(account_usd));used=D(0);unknown=[];details=[]
    for p in positions:
        if p.get('state')!='open':continue
        rate=fx.get(p['instrument_id']);stop=p.get('stop');cost=(costs or {}).get(p['instrument_id'],{})
        fee=p.get('fee_rate',cost.get('fee_rate'));slip=p.get('slippage_rate',cost.get('slippage_rate'));fund=p.get('funding_rate',cost.get('funding_rate'))
        if rate is None or stop is None or any(v is None for v in (fee,slip,fund)):
            unknown.append(p['id']);continue
        entry,stop,qty,fee,slip,fund=map(lambda v:D(str(v)),(p['entry'],stop,p['quantity'],fee,slip,fund))
        loss=max(entry-stop+(entry+stop)*fee+entry*(slip+fund),D(0))*qty*D(str(rate))
        used+=loss;details.append({'id':p['id'],'risk_usd':str(loss),'fee_rate':str(fee),'slippage_rate':str(slip),'funding_rate':str(fund)})
    cap=account*D(str(total_pct))/100;remaining=max(D(0),cap-used)
    configured=account>0
    return {'kind':'user_declared_research_not_broker_balance','account_usd':str(account),'open_risk_usd':str(used),'total_risk_cap_usd':str(cap),'remaining_risk_usd':str(remaining) if configured and not unknown else None,'max_new_plan_risk_usd':str(min(remaining,account*D(str(per_plan_pct))/100)) if configured and not unknown else None,'unknown_positions':unknown,'status':'unconfigured' if not configured else 'incomplete' if unknown else 'available','includes_fees':True,'position_costs':details,'cost_kind':'configured_scenario_not_guaranteed_fill'}

def portfolio():
    from engine.repository import runtime_config
    cfg=runtime_config().execution
    with Session() as s:
        r=s.get(UserPreference,'research_portfolio');p=r.payload if r else {'account_usd':'0','per_plan_pct':'1','total_pct':'5','positions':[]}
        rates={};costs={}
        for row in p['positions']:
            inst=s.get(Instrument,row['instrument_id']);rates[row['instrument_id']]=quote_usd_rate(inst.quote_asset,s,now_ms()) if inst else None
            if inst:costs[inst.id]={'fee_rate':cfg.taker_fee_rates.get(inst.venue),'slippage_rate':cfg.max_spread_bps/10000,'funding_rate':cfg.funding_scenario_rate if inst.market_type in ('linear_perpetual','perpetual') else 0}
        return {**p,'risk':risk_summary(p['account_usd'],p['per_plan_pct'],p['total_pct'],p['positions'],rates,costs)}

def plan_risk(risk,entry,stop,execution):
    """Size only up to both declared risk and tested book notional."""
    if risk['status']!='available':return {'status':risk['status'],'quantity':None,'reason':'未设置研究本金；仅给价格计划，不虚构购买金额' if risk['status']=='unconfigured' else '已有研究仓位风险不完整，暂停新增研究仓位'}
    if not execution or execution.get('status')!='eligible':return {'status':'execution_unavailable','quantity':None}
    fx=execution.get('quote_usd_rate');loss=D(str(entry))-D(str(stop))+D(execution['stop_path_cost'])
    if not fx or loss<=0:return {'status':'execution_unavailable','quantity':None}
    capacity=D(risk['max_new_plan_risk_usd']);unit=loss*D(fx)
    qty=min(capacity/unit,D(execution['base_quantity']))
    return {'status':'available' if qty>0 else 'risk_exhausted','quantity':str(qty),'max_loss_scenario_usd':str(qty*unit),'notional_usd':str(qty*D(str(entry))*D(fx)),'reason':'按声明本金、已有风险及已测试盘口上限测算；不是下单指令'}
