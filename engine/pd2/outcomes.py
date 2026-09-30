"""Forward plan research; fills, missing bars and ambiguous ordering remain separate."""
from decimal import Decimal as D
from sqlalchemy import select
from engine.db import Session
from engine.models import Plan,PlanOutcome,Candle,SignalSnapshot
from engine.adapters.base import now_ms

def evaluate_path(bars,entry,target,stop,start,end,cost=None,entry_expires=None):
    rows=sorted([b for b in bars if start<b['open_time'] and b['close_time']<=end],key=lambda b:b['open_time'])
    if not rows:return {'state':'insufficient_history','mfe':None,'mae':None,'net_return_pct':None}
    if any(a['close_time']!=b['open_time'] for a,b in zip(rows,rows[1:])):return {'state':'data_gap','mfe':None,'mae':None,'net_return_pct':None}
    e,t,s=map(lambda x:D(str(x)),(entry,target,stop));filled=False;mfe=D(0);mae=D(0);fill_time=None
    def result(state,at=None,exit_price=None):
        net=None;gross=None
        if exit_price is not None:
            x=D(str(exit_price));gross=(x/e-1)*100
            if cost and cost.get('fee_rate') is not None and cost.get('slippage_roundtrip_rate') is not None and cost.get('funding_scenario_rate') is not None:
                fee=(e+x)*D(str(cost['fee_rate']))
                other=e*(D(str(cost['slippage_roundtrip_rate']))+D(str(cost['funding_scenario_rate'])))
                net=(x-e-fee-other)/e*100
        return {'state':state,'mfe':str(mfe) if filled else None,'mae':str(mae) if filled else None,'at':at,'fill_time':fill_time,'entry_price':str(e) if filled else None,'exit_price':str(exit_price) if exit_price is not None else None,'gross_return_pct':str(gross) if gross is not None else None,'net_return_pct':str(net) if net is not None else None,'cost_basis':'frozen_entry_execution_scenario_not_actual_exit_liquidity','probability':None}
    for b in rows:
        low,high,opening=map(lambda k:D(str(b[k])),('low','high','open'))
        if not filled:
            if entry_expires and b['open_time']>=entry_expires:return result('unfilled_entry_expired')
            if low<=e<=high:
                filled=True;fill_time=b['open_time']
                if low<=s or high>=t:
                    out=result('entry_bar_order_unknown');out['return_bounds_gross_pct']=[str((s/e-1)*100),str((t/e-1)*100)];return out
            continue
        mfe=max(mfe,(high/e-1)*100);mae=min(mae,(low/e-1)*100)
        if opening<=s:return result('stop_gap',b['open_time'],opening)
        if opening>=t:return result('target_gap',b['open_time'],t)
        if high>=t and low<=s:
            out=result('same_bar_order_unknown');out['return_bounds_gross_pct']=[str((s/e-1)*100),str((t/e-1)*100)];return out
        if low<=s:return result('stop_first',b['close_time'],s)
        if high>=t:return result('target_first',b['close_time'],t)
    if rows[-1]['close_time']<end-3600000:return result('incomplete_horizon')
    return result('expired' if filled else 'unfilled',rows[-1]['close_time'],rows[-1].get('close') if filled else None)

def first_recommendation(session, plan_id):
    return session.scalar(select(SignalSnapshot).where(
        SignalSnapshot.plan_id==plan_id,
        SignalSnapshot.payload['action']['code'].as_string().in_(('participate','trial')),
    ).order_by(SignalSnapshot.as_of,SignalSnapshot.id).limit(1))

def update_outcomes():
    now=now_ms();count=0
    with Session.begin() as s:
        for plan in s.scalars(select(Plan)):
            target=plan.payload.get('original_t1')
            recommendation=first_recommendation(s,plan.id)
            for horizon in plan.payload['config']['validation']['horizons_hours']:
                old=s.get(PlanOutcome,(plan.id,horizon))
                if old and old.state not in ('immature','not_recommended'):continue
                start=recommendation.as_of if recommendation else plan.payload.get('created_at',now)
                end=start+horizon*3600000
                if not target:result={'state':'target_missing'}
                elif not recommendation:result={'state':'not_recommended'}
                elif now<end:result={'state':'immature','matures_at':end}
                else:
                    candles=list(s.scalars(select(Candle).where(Candle.instrument_id==plan.execution_instrument_id,Candle.resolution=='60',Candle.open_time>start,Candle.close_time<=end,Candle.is_closed==True,Candle.received_at<=now).order_by(Candle.open_time)))
                    bars=[{'open_time':c.open_time,'close_time':c.close_time,'open':str(c.open),'low':str(c.low),'high':str(c.high),'close':str(c.close)} for c in candles]
                    result=evaluate_path(bars,plan.payload['evidence']['entry_zone'][1],target['low'],plan.payload['original_protection'],start,end,recommendation.payload.get('execution'),plan.expires_at)
                result.update(kind='forward_frozen_plan_research',known_at=start,horizon_hours=horizon,original_t1=target,original_stop=plan.payload['original_protection'],probability=None,validation_status='unvalidated',cost_model=plan.payload['config']['execution'])
                s.merge(PlanOutcome(plan_id=plan.id,horizon_hours=horizon,state=result['state'],evaluated_at=now,payload=result));count+=1
    return count
