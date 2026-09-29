"""Explicit actions with executable quote/depth/cost gates; no probability fabrication."""
from decimal import Decimal as D

def walk_book(levels, quote_amount):
    remain=D(str(quote_amount));quantity=D(0);spent=D(0)
    for price,size in levels:
        p,q=D(str(price)),D(str(size))
        if p<=0 or q<0:raise ValueError("invalid_depth")
        cost=min(remain,p*q);quantity+=cost/p;spent+=cost;remain-=cost
        if remain<=0:break
    return {"complete":remain<=0,"average":str(spent/quantity) if quantity else None,
            "quantity":str(quantity),"quote_filled":str(spent)}

def sell_quantity(levels, quantity):
    remain=D(str(quantity));proceeds=D(0)
    for price,size in levels:
        p,q=D(str(price)),D(str(size))
        if p<=0 or q<0:raise ValueError("invalid_depth")
        taken=min(remain,q);proceeds+=taken*p;remain-=taken
        if remain<=0:break
    filled=D(str(quantity))-remain
    return {"complete":remain<=0,"average":str(proceeds/filled) if filled else None}

def execution_cost(entry,stop,target,book,now,notional="1000",fee_rate=None,funding_cost_rate=None,min_r="1.8",max_spread_bps="30",max_age_ms=10000):
    issues=[]
    if not book or not 0<=now-book.get("received_at",0)<=max_age_ms or not 0<=now-book.get("observed_at",book.get("received_at",0))<=max_age_ms:
        return {"status":"stale","net_r":None,"reasons":["原生盘口超过10秒或尚未采集"]}
    if book.get("scope")!="execution_market":issues.append("聚合盘口不能用于本市场可成交性")
    if fee_rate is None:issues.append("手续费情景尚未配置")
    if funding_cost_rate is None:issues.append("资金费用情景尚未配置")
    e,s,t=map(lambda x:D(str(x)),(entry,stop,target))
    if not t>e>s>0:issues.append("目标/保护与参与带位置不成立")
    if not book.get("asks") or not book.get("bids"):issues.append("买卖盘口缺失")
    if issues:return {"status":"unavailable","net_r":None,"reasons":issues}
    buy=walk_book(book["asks"],notional);sell=sell_quantity(book["bids"],buy["quantity"])
    if not buy["complete"] or not sell["complete"]:return {"status":"insufficient_depth","net_r":None,"reasons":["测试金额超过可见盘口深度"]}
    ask,bid=D(book["asks"][0][0]),D(book["bids"][0][0])
    if ask<bid or bid<=0:return {"status":"invalid","net_r":None,"reasons":["盘口交叉或价格无效"]}
    spread_bps=(ask-bid)/ask*10000
    if spread_bps>D(str(max_spread_bps)):return {"status":"wide_spread","net_r":None,"spread_bps":str(spread_bps),"reasons":["买卖价差超过配置上限"]}
    slip=(D(buy["average"])/ask-1)+(1-D(sell["average"])/bid)+(ask-bid)/ask
    fee=max(D(str(fee_rate)),D(0));fund=max(D(str(funding_cost_rate)),D(0))
    # Conservative round-trip scenario using current book, not a prediction of exit depth.
    win_cost=(e+t)*fee+e*(slip+fund);loss_cost=(e+s)*fee+e*(slip+fund)
    net=(t-e-win_cost)/(e-s+loss_cost)
    return {"status":"eligible" if net>=D(str(min_r)) else "low_net_r","net_r":str(net),
        "quote_price":str(ask),"bid":str(bid),"received_at":book["received_at"],"probe_notional":str(notional),
        "spread_bps":str(spread_bps),"base_quantity":buy["quantity"],"fee_rate":str(fee),"funding_scenario_rate":str(fund),"slippage_roundtrip_rate":str(slip),
        "fee_kind":"configured_research_scenario","formula":"(T-E-win_cost)/(E-S+loss_cost)",
        "profit_path_cost":str(win_cost),"stop_path_cost":str(loss_cost),
        "reasons":[] if net>=D(str(min_r)) else ["扣费后盈亏比低于本模板门槛"]}

def decide(evidence,phase,quote,now,execution=None,style="aggressive",max_quote_age_ms=15000):
    zone=evidence.get("entry_zone");stop=evidence.get("protection");trigger=evidence.get("trigger")
    price=D(str(quote["price"])) if quote and quote.get("price") is not None else None
    quote_fresh=bool(quote and quote.get("price_kind") in ("ask","last") and 0<=now-quote.get("as_of",0)<=max_quote_age_ms)
    reasons=[]
    if phase in ("invalidated","ended") or (quote_fresh and price is not None and stop and price<=D(str(stop))):reasons.append("protection_or_invalidation")
    if not quote_fresh:reasons.append("quote_stale")
    if phase=="weakening":reasons.append("structure_weakening")
    if not trigger:reasons.append("trigger_pending")
    if evidence.get("expires_at") and now>=evidence["expires_at"]:reasons.append("entry_expired")
    if quote_fresh and zone and price>D(str(zone[1])):reasons.append("above_zone")
    if quote_fresh and zone and price<D(str(zone[0])):reasons.append("below_zone")
    if not execution or execution.get("status")!="eligible":reasons.append("execution_not_eligible")
    def answer(code,label,reason):
        return {"code":code,"label":label,"reason":reason,"reason_codes":list(dict.fromkeys(reasons)),
            "action_existing":"按原保护管理" if phase not in ("invalidated","ended") else "退出旧计划",
            "validation_status":"unvalidated","probability":None}
    if phase in ("invalidated","ended") or (quote_fresh and price is not None and stop and price<=D(str(stop))):
        reasons.append("protection_or_invalidation");return answer("ended","停止旧计划",evidence.get("reason","保护已触发"))
    if not quote_fresh:
        reasons.append("quote_stale");return answer("paused","暂停参与","可成交报价超过15秒或缺失；保留原计划保护")
    if phase=="weakening":
        reasons.append("structure_weakening");return answer("paused","暂停新参与",evidence.get("reason","结构已转弱"))
    if not trigger:
        reasons.append("trigger_pending");return answer("waiting","等待突破",f'需闭合K线越过 {evidence.get("box",{}).get("trigger_price","待计算")} 并满足成交门槛')
    if not zone or not stop or not evidence.get("expires_at"):
        reasons.append("plan_incomplete");return answer("paused","计划数据不完整","参与区、保护或有效期缺失")
    if now>=evidence["expires_at"]:
        reasons.append("entry_expired");return answer("expired","等待新计划","入场窗口已结束；原趋势和保护继续独立跟踪")
    if price>D(str(zone[1])):
        reasons.append("above_zone");return answer("no_chase","不建议追入",f"现价超过 {zone[0]}–{zone[1]}，等待独立回踩触发")
    if price<D(str(zone[0])):
        reasons.append("below_zone");return answer("waiting","等待回到参与区",f"等待价格回到 {zone[0]}–{zone[1]}，原结构仍须有效")
    if style=="confirmed" and phase=="early_trigger":
        reasons.append("confirmation_required");return answer("waiting","等待4H确认","确认型方案不执行1H提前布局")
    if not execution or execution.get("status")!="eligible":
        reasons.append("execution_not_eligible")
        detail="；".join((execution or {}).get("reasons",["盘口与成本尚无可用结果"]))
        return answer("no_chase" if execution and execution.get("status")=="low_net_r" else "paused","暂不参与",detail)
    early=phase=="early_trigger"
    return answer("trial" if early else "participate","建议提前布局（1H触发）" if early else "建议现在布局（结构确认）",
        "当前位于参与区，触发、流动性和扣费后盈亏比均符合本模板；保护价 "+str(stop))
