"""Closed-bar, frozen-box research signals. No fabricated execution approval."""
from decimal import Decimal as D
from statistics import median
from hashlib import sha256

VERSION="box-20-60-v1"
FOUR_HOURS=14400_000

def closed(bars,now):
    return sorted([b for b in bars if b["is_closed"] and b["close_time"]<=now],key=lambda b:b["open_time"])

def atr(bars,period=14):
    if len(bars)<period+1:return None
    true_ranges=[max(D(b["high"])-D(b["low"]),abs(D(b["high"])-D(previous["close"])),abs(D(b["low"])-D(previous["close"]))) for previous,b in zip(bars,bars[1:])]
    result=sum(true_ranges[:period])/period
    for value in true_ranges[period:]:result=(result*(period-1)+value)/period
    return result if result>0 else None

def pivots(bars,key):
    points=[]
    for i in range(2,len(bars)-2):
        v=D(bars[i][key]);neighbors=[D(bars[j][key]) for j in (i-2,i-1,i+1,i+2)]
        if (key=="high" and all(v>x for x in neighbors)) or (key=="low" and all(v<x for x in neighbors)):
            points.append({"index":i,"price":v,"time":bars[i]["open_time"],"known_at":bars[i+2]["close_time"]})
    return points

def cluster(points,tolerance):
    candidates=[]
    for anchor in points:
        members=[p for p in points if abs(p["price"]-anchor["price"])<=tolerance]
        separated=[]
        for p in sorted(members,key=lambda p:p["index"]):
            if not separated or p["index"]-separated[-1]["index"]>=2:separated.append(p)
        if len(separated)>=2 and max(p["price"] for p in separated)-min(p["price"] for p in separated)<=2*tolerance:
            candidates.append(separated)
    if not candidates:return None
    best=max(candidates,key=lambda ps:(len(ps),max(p["index"] for p in ps)))
    return median(p["price"] for p in best),len(best)

def find_box(bars,lookback):
    if len(bars)<max(lookback,30):return None
    sample=bars[-lookback:]
    if any(b["open_time"]-a["open_time"]!=FOUR_HOURS for a,b in zip(sample,sample[1:])):return None
    a=atr(bars)
    if a is None:return None
    upper=cluster(pivots(sample,"high"),a*D("0.5"));lower=cluster(pivots(sample,"low"),a*D("0.5"))
    if not upper or not lower:return None
    u,l=upper[0],lower[0];height=u-l
    if height<=a:return None
    values=[D(b["close"]) for b in sample]
    inside=D(sum(l<=v<=u for v in values))/len(values)
    drift=abs(median(values[:len(values)//2])-median(values[len(values)//2:]))
    if inside<D("0.8") or drift>height*D("0.5"):return None
    return {"upper":str(u),"lower":str(l),"atr":str(a),"lookback":lookback,
            "start_time":sample[0]["open_time"],"end_time":sample[-1]["close_time"],
            "upper_touches":upper[1],"lower_touches":lower[1],"inside_ratio":str(inside),
            "trigger_price":str(u+a*D("0.25")),"rule_version":VERSION}

def volume_multiple(bar,prior):
    from engine.features.numeric import relative_volume
    return relative_volume(bar.get('quote_volume'),[b.get('quote_volume') for b in prior],20,'median')

def target(bars,entry,timeframe=None):
    a=atr(bars)
    if a is None:return None
    above=[p for p in pivots(bars,"high") if p["price"]-a*D("0.25")>entry]
    if not above:return None
    anchor=min(above,key=lambda p:p["price"])
    return {"low":str(anchor["price"]-a*D("0.25")),"high":str(anchor["price"]+a*D("0.25")),
            "kind":"confirmed_pivot","method":"confirmed_pivot_atr_zone","timeframe":timeframe,
            "known_at":anchor["known_at"]}

def candidate(instrument,bars4,bars1,now):
    four=closed(bars4,now)
    if not four or now-four[-1]["close_time"]>FOUR_HOURS+600_000:return None
    # Find a recently completed structure. All pivots are confirmed before its end.
    for offset in range(0,min(7,len(four)-30)):
        history=four if offset==0 else four[:-offset]
        for size in (20,60):
            box=find_box(history,size)
            if box:
                from engine.pd2.box_quality import audit
                qualification=audit(box,history)
                if qualification['status']!='verified':continue
                box={**box,'qualification':qualification}
                key=sha256((instrument+str(box)).encode()).hexdigest()[:24]
                state=evaluate(box,bars4,bars1,now)
                if state["phase"] not in ("invalidated","ended"):
                    return {"id":"box-"+key,"box":box,**state}
    return None

def evaluate(box,bars4,bars1,now,recorded=None):
    four=closed(bars4,now);one=closed(bars1,now)
    u,l,a=(D(box[k]) for k in ("upper","lower","atr"));threshold=D(box["trigger_price"])
    post=[b for b in four if b["close_time"]>box["end_time"]]
    confirmed=None;early=None;volume=None;unconfirmed_breakouts=[]
    for i,b in enumerate(four):
        if b["close_time"]<=box["end_time"]:continue
        multiple=volume_multiple(b,four[:i])
        if D(b["close"])>threshold:
            unconfirmed_breakouts.append({"close_time":b["close_time"],"close":str(b["close"]),"volume_multiple":str(multiple) if multiple is not None else None})
            if multiple is not None and multiple>=D("1.5"):
                confirmed=b;volume=multiple;break
    for i,b in enumerate(one):
        if b["close_time"]<=box["end_time"]:continue
        if confirmed and b["close_time"]>=confirmed["close_time"]:break
        multiple=volume_multiple(b,one[:i])
        if D(b["close"])>threshold and multiple is not None and multiple>=D("1.5"):
            early=b;break
    saved=(recorded or {}).get("trigger")
    # Live plans retain their original trigger after that candle leaves the cache.
    if saved:
        if saved["resolution"]=="240":
            confirmed=saved;volume=D(recorded["volume_multiple"])
            early=None
        elif early is None or saved["close_time"]<early["close_time"]:
            early=saved
    trigger=confirmed or early
    pending_volume=volume_multiple(four[-1],four[:-1]) if four else None
    pending_state=("close_above_volume_unconfirmed" if unconfirmed_breakouts else
                   "intrabar_above_close_rejected" if any(D(b["high"])>threshold and D(b["close"])<=threshold for b in post) else
                   "near_trigger" if post and D(post[-1]["close"])>=u else "waiting_breakout")
    state={"phase":"structure_pending","reason":"等待 4H 收盘突破与成交量确认", "trigger":None,
           "confirmation":None,"volume_multiple":None,"pending_volume_multiple":str(pending_volume) if pending_volume is not None else None,
           "pending_state":pending_state,"unconfirmed_breakouts":unconfirmed_breakouts[-3:],
           "trigger_requirements":{"timeframe":"240","close_above":str(threshold),"relative_volume_min":"1.5","volume_baseline":"prior_20_closed_4h_quote_volume_median"},
           "invalidation":{"price":str(l),"timeframe":"240","condition":"close_below","method":"frozen_box_lower"},
           "protection_reference":{"price":str(u-a*D("0.25")),"status":"estimated","method":"box_upper_minus_0.25_atr","finalized_when":"trigger_confirmed"},
           "protection":None,"entry_zone":None,"expires_at":None}
    if trigger:
        is_confirmed=confirmed is not None
        trigger_resolution="240" if is_confirmed else "60"
        applicable=four if is_confirmed else one
        prior=[b for b in applicable if b["close_time"]<trigger["close_time"]]
        trigger_atr=atr(prior) if not is_confirmed else a
        trigger_atr=trigger_atr or a
        lows=pivots(prior,"low")
        protection=(u-a*D("0.25")) if is_confirmed else (lows[-1]["price"]-trigger_atr*D("0.25") if lows else D(trigger["close"])-trigger_atr*D("1.5"))
        state.update(phase="structure_confirmed" if is_confirmed else "early_trigger",
            reason="4H 收盘突破箱体上沿，成交达到前 20 根中位数的 1.5 倍" if is_confirmed else "1H 收盘提前突破，4H 尚未确认",
            trigger={"open_time":trigger["open_time"],"close_time":trigger["close_time"],"close":str(trigger["close"]),"resolution":trigger_resolution},
            confirmation=confirmed["close_time"] if confirmed else None,
            volume_multiple=str(volume if is_confirmed else volume_multiple(early,[b for b in one if b["close_time"]<early["close_time"]])),
            protection=str(protection),entry_zone=[str(threshold),str(threshold+trigger_atr*D("0.5"))],
            expires_at=trigger["close_time"]+(FOUR_HOURS if is_confirmed else 3600_000))
        if saved and state["trigger"]==saved:
            for key in ("protection","entry_zone","expires_at","volume_multiple"):
                state[key]=recorded[key]
            if recorded.get("phase") in ("trend_continuing","weakening"):
                state.update(phase=recorded["phase"],reason=recorded["reason"])
        # An early event invalidated before the 4H confirmation must not be resurrected.
        if early:
            early_revoke=next((b for b in one if b["close_time"]>early["close_time"] and (not confirmed or b["close_time"]<=confirmed["close_time"]) and D(b["close"])<u),None)
            if early_revoke:
                state.update(phase="invalidated",reason="1H 收盘跌回冻结上沿，早期突破已撤销",invalidated_at=early_revoke["close_time"])
                return state
        revoke=next((b for b in applicable if b["close_time"]>trigger["close_time"] and D(b["close"])<u),None)
        if revoke:
            state.update(phase="invalidated",reason=("箱体下沿已失守" if D(revoke["close"])<l else "收盘跌回箱体上沿下方，本次突破撤销"),invalidated_at=revoke["close_time"])
        elif is_confirmed:
            after=[b for b in four if b["close_time"]>confirmed["close_time"]]
            lowpoints=pivots(after,"low")
            if len(lowpoints)>=2 and lowpoints[-1]["price"]>lowpoints[-2]["price"]:
                state.update(phase="trend_continuing",reason="4H 突破后维持上沿之上，并形成已确认抬高低点",confirmed_low=str(lowpoints[-1]["price"]))
                if after and D(after[-1]["close"])<lowpoints[-1]["price"]:
                    state.update(phase="weakening",reason="4H 收盘跌破最近已确认的抬高低点")
    elif any(D(b["close"])<l for b in post):state.update(phase="invalidated",reason="4H 收盘跌破箱体下沿，待突破结构失效")
    elif now-box["end_time"]>20*FOUR_HOURS:state.update(phase="ended",reason="待突破结构已超过 20 根 4H 柱观察期限")
    return state
