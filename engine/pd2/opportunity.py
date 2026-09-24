"""Price-derived objectives and explicit sample-high/ATH provenance."""
from decimal import Decimal as D
from copy import deepcopy

def price_distance(price,target):
    p,t=D(str(price)),D(str(target))
    if p<=0 or t<=0:return None
    return {'price_multiple':str(t/p),'remaining_upside_pct':str((t/p-1)*100),'drawdown_pct':str((p/t-1)*100)}

def enrich(opportunity,assessments,bars,price,now,quote_asset,action=None,valuation=None):
    op=deepcopy(opportunity);p=D(str(price)) if price is not None else None
    if not p or p<=0:return op
    available=[b for rows in bars.values() for b in rows if b.get('is_closed') and b['close_time']<=now and b.get('received_at',b['close_time'])<=now]
    high=max(available,key=lambda b:D(b['high']),default=None)
    op['ath']={'status':'not_verified','price':None,'reason':'尚无经核验的全历史/迁移调整记录；下方仅为当前市场已采集样本最高价'}
    op['sample_high']=({'price':high['high'],'time':high['open_time'],'coverage_start':min(b['open_time'] for b in available),'coverage_end':max(b['close_time'] for b in available),'quote_asset':quote_asset,'kind':'sample_wick_high_not_ath',**price_distance(p,high['high'])} if high else None)
    by_tf={a['timeframe']:a for a in assessments};current=[]
    for a in assessments:
        levels=sorted([v for v in a.get('levels',[]) if v['kind']=='resistance' and D(v['low'])>p],key=lambda v:D(v['low']))
        target=deepcopy(levels[0]) if levels and a['status']=='fresh' else None
        if target:target.update(price_distance(p,target['low']))
        current.append({'timeframe':a['timeframe'],'target':target,'direction':a['direction'],'status':a['status'],'missing_reason':a.get('reason') or ('无上方有效结构阻力' if not target else None),'condition':('该周期收盘维持原支撑，前序阻力突破后才激活远目标' if target else None)})
    op['timeframe_spaces']=current
    daily=by_tf.get('1D',{});weekly=by_tf.get('1W',{})
    primary=op.get('daily') if daily.get('direction')=='up' and daily.get('status')=='fresh' else op.get('t1')
    op['primary']=primary;op['primary_timeframe']=primary['timeframe'] if primary else None
    maximum=op.get('highest_conditional_target')
    # The price zone remains a hypothesis until every recorded gate is met.
    if maximum:
        gates=[]
        for dependency in maximum.get('dependencies',[]):
            a=by_tf.get(dependency['timeframe'],{});last=a.get('latest',{}).get('close')
            met=a.get('status')=='fresh' and last is not None and D(str(last))>D(dependency['price'])
            gates.append({**dependency,'met':met,'known_at':a.get('known_at')})
        if maximum['timeframe']=='1W':gates.append({'timeframe':'1W','condition':'周线已闭合结构上行且MACD柱值为正','met':weekly.get('status')=='fresh' and weekly.get('direction')=='up' and D(str(weekly.get('latest',{}).get('histogram') or 0))>0,'known_at':weekly.get('known_at')})
        maximum['dependencies']=gates;maximum['activation_status']='active' if all(g['met'] for g in gates) else 'conditional'
        maximum.update(price_distance(p,maximum['low']))
    op['space_classification']='当前不支持倍数判断'
    if primary and D(primary['low'])>=2*p and (action or {}).get('code') in ('trial','participate'):op['space_classification']='本轮具备倍数级结构空间'
    elif maximum and D(maximum['low'])>=2*p:op['space_classification']='有倍数延伸条件，须先满足列示关口'
    elif high and D(high['high'])>=2*p:op['space_classification']='仅有远端样本修复空间，当前无倍数交易依据'
    op['target_reached']={k:bool(op.get(k) and p>=D(op[k]['low'])) for k in ('t1','daily','weekly')}
    op['position_management']='第一目标已到，按计划部分兑现；余仓继续跟踪原保护和日周结构' if op['target_reached']['t1'] else '未到第一目标，按原保护管理；不因远目标扩大而放松保护'
    supply=(valuation or {}).get('circulating_supply');fx=(valuation or {}).get('quote_usd_rate')
    op['supply_scenario']={'status':'future_supply_unknown','reason':'未取得目标时点解锁计划；静态市值不表示所需资金流入','static_current_supply':str(supply) if supply else None,'static_primary_market_cap_usd':str(D(primary['low'])*D(str(supply))*D(str(fx))) if supply and fx and primary else None,'assumption':'当前流通供应与报价币USD汇率不变；不是目标时点市值预测'}
    return op
