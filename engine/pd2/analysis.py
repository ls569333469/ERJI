"""Causal, closed-bar analysis shared by charts, decisions and replay (PD2 4.15)."""
from decimal import Decimal as D
from copy import deepcopy
from hashlib import sha256
from statistics import median
import numpy as np
import talib

VERSION = "pd2-structure-v1"
DURATIONS = {str(m):m*60000 for m in (15,30,60,120,240,480,720)} | {"1D":86400000,"1W":604800000}
LABELS = {str(m):(str(m//60)+"H" if m%60==0 else str(m)+"m") for m in (15,30,60,120,240,480,720)} | {"1D":"1D","1W":"1W"}

def number(value):
    if value is None: return None
    result = float(value)
    return None if not np.isfinite(result) else format(D(str(result)), "f")

def closed_bars(bars, now):
    return sorted((b for b in bars if b["is_closed"] and b["close_time"] <= now
                   and b.get("received_at", b["close_time"]) <= now), key=lambda b:b["open_time"])

def aggregate(bars, resolution, now):
    """No partial 2H/day/week is ever labelled closed. Monday UTC for weeks."""
    duration = DURATIONS[resolution]
    offset = 345600000 if resolution == "1W" else 0
    groups = {}
    for b in bars:
        start = ((b["open_time"]-offset)//duration)*duration+offset
        groups.setdefault(start,[]).append(b)
    result=[]
    for start, group in sorted(groups.items()):
        group.sort(key=lambda b:b["open_time"])
        if group[0]["open_time"] != start: continue
        contiguous = all(a["close_time"] == b["open_time"] for a,b in zip(group, group[1:]))
        if not contiguous: continue
        complete = group[-1]["close_time"] == start+duration and all(b["is_closed"] for b in group) and start+duration<=now
        # Incomplete tail may be drawn, but never used in closed-bar analysis.
        if not complete and group[-1]["close_time"] < now: continue
        result.append({**group[0],"resolution":resolution,"open_time":start,"close_time":start+duration,
            "open":group[0]["open"],"close":group[-1]["close"],
            "high":str(max(D(b["high"]) for b in group)), "low":str(min(D(b["low"]) for b in group)),
            "base_volume":str(sum(D(b["base_volume"]) for b in group)) if all(b.get("base_volume") is not None for b in group) else None,
            "quote_volume":str(sum(D(b["quote_volume"]) for b in group)) if all(b.get("quote_volume") is not None for b in group) else None,
            "is_closed":complete,"received_at":max(b.get("received_at",b["close_time"]) for b in group)})
    return result

def pivots(bars, key, left=2, right=2):
    points=[]
    for i in range(left,len(bars)-right):
        values=[D(b[key]) for b in bars[i-left:i+right+1]]
        extreme=max(values) if key=="high" else min(values)
        # Last equal extreme in the entire window wins; right-side bars confirm it.
        if D(bars[i][key])!=extreme or any(v==extreme for v in values[left+1:]): continue
        points.append({"price":str(extreme),"pivot_time":bars[i]["open_time"],
            "known_at":max(bars[i+right]["close_time"],max(b.get("received_at",b["close_time"]) for b in bars[i-left:i+right+1])),
            "index":i,"source_bar_ids":[b["open_time"] for b in bars[i-left:i+right+1]]})
    return points

def technical(bars, resolution, now, tick="0.00000001", config=None):
    rows=closed_bars(bars,now)
    # Do not silently calculate through a missing interval.
    duration=DURATIONS[resolution]
    gaps=[b["open_time"] for a,b in zip(rows,rows[1:]) if b["open_time"]-a["open_time"]!=duration]
    if gaps:
        rows=[b for b in rows if b["open_time"]>=gaps[-1]]
    if not rows:
        return {"timeframe":resolution,"status":"insufficient_history","reason":"没有可用闭合K线","series":[],"levels":[],"direction":"unknown","validation_status":"unvalidated"}
    x=lambda key:np.array([float(b[key]) for b in rows],dtype=float)
    close,high,low,volume=x("close"),x("high"),x("low"),np.array([float(b.get("base_volume") or 0) for b in rows])
    ema20,ema50=talib.EMA(close,20),talib.EMA(close,50)
    macd,signal,hist=talib.MACD(close,12,26,9)
    atr=talib.ATR(high,low,close,14)
    rsi=talib.RSI(close,14);adx=talib.ADX(high,low,close,14)
    upper,middle,lower=talib.BBANDS(close,20,2,2,0)
    series=[]
    for i,b in enumerate(rows):
        series.append({"time":b["open_time"],"close_time":b["close_time"],"known_at":max(b["close_time"],b.get("received_at",0)),
          "is_closed":True,"close":b["close"],**{name:number(values[i]) for name,values in (
          ("ema20",ema20),("ema50",ema50),("macd",macd),("signal",signal),("histogram",hist),
          ("atr14",atr),("rsi14",rsi),("adx14",adx),("bb_upper",upper),("bb_middle",middle),("bb_lower",lower))}})
    a=D(str(atr[-1])) if np.isfinite(atr[-1]) and atr[-1]>0 else None
    tick=D(str(tick));p=D(rows[-1]["close"])
    left=getattr(config,"pivot_left",2);right=getattr(config,"pivot_right",2)
    factor=D(str(getattr(config,"zone_atr_multiplier",.25)))
    tops,bottoms=pivots(rows,"high",left,right),pivots(rows,"low",left,right)
    direction="unknown"
    if len(tops)>=2 and len(bottoms)>=2:
        dh=D(tops[-1]["price"])-D(tops[-2]["price"]);dl=D(bottoms[-1]["price"])-D(bottoms[-2]["price"])
        direction="up" if dh>tick and dl>tick else "down" if dh < -tick and dl < -tick else "mixed"
        if direction=="up" and p<D(bottoms[-1]["price"]):direction="broken"
    levels=[];excluded_levels=[]
    if a:
        width=max(2*tick,a*factor)
        for kind,points in (("resistance",tops),("support",bottoms)):
            for anchor in points[-30:]:
                # PD2 4.15: use the latest closed ATR at creation of this price snapshot.
                anchor_atr=atr[-1]
                if not np.isfinite(anchor_atr) or anchor_atr<=0:continue
                width=max(2*tick,D(str(anchor_atr))*factor)
                anchor_price=D(anchor["price"]);lo=anchor_price-width;hi=anchor_price+width
                if lo<=0:continue
                after=[b for b in rows if b["close_time"]>anchor["known_at"]]
                role=level_role(kind,lo,hi,after)
                active_kind=role['kind']
                level={"id":sha256(f'{resolution}:{kind}:{anchor["pivot_time"]}'.encode()).hexdigest()[:20],
                  "kind":active_kind,"original_kind":kind,"low":str(lo),"high":str(hi),"timeframe":resolution,"method":"confirmed_pivot",
                  "label":LABELS[resolution]+("阻力转支撑" if kind!=active_kind and active_kind=="support" else "支撑转阻力" if kind!=active_kind else "前高阻力" if kind=="resistance" else "前低支撑"),
                  "anchors":[anchor],"known_at":anchor["known_at"],"atr":number(anchor_atr),"tick_size":str(tick),"revision":rows[-1]["close_time"],"created_at":now,
                  "formula":f"p ± max(2*tick, {factor}*ATR14_at_snapshot_latest_close)","status":role["status"],"role_events":role["events"],"validation_status":"unvalidated"}
                if role["events"]:level["known_at"]=max(level["known_at"],role["events"][-1]["known_at"])
                (levels if role["status"]=="confirmed" else excluded_levels).append(level)
    last=series[-1];previous=series[-2] if len(series)>1 else {}
    cross=last["histogram"] is not None and previous.get("histogram") is not None and D(last["histogram"])>0>=D(previous["histogram"])
    momentum="unknown"
    if last["histogram"] is not None and previous.get("histogram") is not None:
        momentum="strengthening" if D(last["histogram"])>D(previous["histogram"]) else "weakening"
    qv=[D(b["quote_volume"]) for b in rows[-21:-1] if b.get("quote_volume") is not None]
    vm=D(rows[-1]["quote_volume"])/median(qv) if len(qv)==20 and median(qv)>0 and rows[-1].get("quote_volume") is not None else None
    fib=[]
    if tops and bottoms:
        bottom=bottoms[-1];before=[v for v in tops if v["pivot_time"]<bottom["pivot_time"]]
        if before:
            top=before[-1];h,l=D(top["price"]),D(bottom["price"])
            if h>l and all(D(b["low"])>=l for b in rows[bottom["index"]+1:]) and p<h:
                fib=[{"ratio":str(r),"price":str(l+r*(h-l)),"anchors":[top,bottom],"kind":"auxiliary_retracement"}
                    for r in [D(".382"),D(".5"),D(".618")]]
    stale=now-rows[-1]["close_time"]>duration+180000
    return {"timeframe":resolution,"status":"insufficient_history" if len(rows)<50 else "stale" if stale else "fresh",
      "reason":"EMA50预热不足；其他已就绪指标仍可查看" if len(rows)<50 else "最近闭合K线过期" if stale else None,
      "bar_count":len(rows),"gap_count":len(gaps),"closed_at":rows[-1]["close_time"],
      "known_at":max(b.get("received_at",b["close_time"]) for b in rows),
      "direction":direction,"momentum":momentum,"macd_cross_confirmed":bool(cross),
      "volume_multiple":str(vm) if vm is not None else None,
      "roc_5_pct":number((close[-1]/close[-6]-1)*100) if len(close)>5 and close[-6]>0 else None,
      "macd_atr":number(hist[-1]/atr[-1]) if a and np.isfinite(hist[-1]) else None,"latest":last,"series":series,
      "levels":levels,"excluded_levels":excluded_levels,"fibonacci":fib,"validation_status":"unvalidated","algorithm_version":VERSION}

def choose_targets(assessments, entry, box=None,reference_price=None,box_confirmed=False):
    """Nearest resistance cannot be skipped to improve R. Projections are conditional."""
    e=D(str(entry));all_levels=[deepcopy(v) for a in assessments if a.get("status","fresh")=="fresh" for v in a.get("levels",[]) if v["kind"]=="resistance"]
    touching=[v for v in all_levels if D(v["low"])<=e<=D(v["high"])]
    above=sorted((v for v in all_levels if D(v["low"])>e),key=lambda v:(D(v["low"]),v["timeframe"]))
    projection=None
    if box:
        u,l=D(box["upper"]),D(box["lower"]);price=2*u-l
        if u>l>0 and price>e:
            projection={"id":"box-measured","low":str(price),"high":str(price),"timeframe":"240",
                "method":"box_projection","label":"箱高测算目标","status":"active" if box_confirmed else "conditional","known_at":box["end_time"],
                "formula":"U + (U - B)","inputs":{"U":str(u),"B":str(l)}}
    first=above[0] if above else projection if box_confirmed else None
    daily=next((v for v in above if v["timeframe"]=="1D"),None)
    weekly=next((v for v in above if v["timeframe"]=="1W"),None)
    for v in [first,daily,weekly]:
        if v:
            v["remaining_upside_pct"]=str((D(v["low"])/D(str(reference_price or e))-1)*100)
            v["price_multiple"]=str(D(v["low"])/D(str(reference_price or e)))
    highest=max((v for v in (first,daily,weekly) if v),key=lambda v:D(v["low"]),default=None)
    if highest:
        highest=deepcopy(highest)
        highest["activation_status"]="conditional"
        highest["dependencies"]=[{"level_id":v["id"],"price":v["high"],"timeframe":v["timeframe"],"condition":"所属周期收盘突破阻力上沿"} for v in above if D(v["low"])<D(highest["low"])]
    supports=sorted((deepcopy(v) for a in assessments if a.get("status")=="fresh" for v in a.get("levels",[]) if v["kind"]=="support" and D(v["high"])<D(str(reference_price or e))),key=lambda v:D(v["high"]),reverse=True)
    return {"selected_support":supports[0] if supports else None,"t1":first,"daily":daily,"weekly":weekly,"highest_conditional_target":highest,
        "intermediate_levels":above,"entry_obstructions":touching,"box_projection":projection,
        "highest_condition":"先突破沿途阻力，且目标所属周期结构维持有效；周线延伸须周收盘确认",
        "validation_status":"unvalidated"}

def trailing_protection(bars, previous, original, now, period=22, multiple="3"):
    rows=closed_bars(bars,now);floor=D(str(original))
    if previous is not None:floor=max(floor,D(str(previous)))
    if len(rows)<period:return str(floor)
    a=talib.ATR(np.array([float(b["high"]) for b in rows]),np.array([float(b["low"]) for b in rows]),np.array([float(b["close"]) for b in rows]),14)[-1]
    if not np.isfinite(a):return str(floor)
    candidate=max(D(b["high"]) for b in rows[-period:])-D(str(a))*D(multiple)
    return str(max(floor,candidate))

def weekly_cross_gate(bars, now):
    rows=closed_bars(bars,now)
    if len(rows)<35:return {"status":"insufficient_history","price":None}
    c=np.array([float(b["close"]) for b in rows])
    _,_,hist=talib.MACD(c,12,26,9)
    if not np.isfinite(hist[-1]):return {"status":"insufficient_history","price":None}
    if hist[-1]>0:return {"status":"already_above","price":None}
    # MACD seeds its fast EMA at the slow lookback; standalone EMA12 is not equivalent.
    # Its next histogram is affine in the next close; two actual TA-Lib evaluations
    # produce a threshold with precisely the same initialization as displayed MACD.
    x0=max(float(c[-1]),1e-12);x1=x0*2
    h0=talib.MACD(np.append(c,x0),12,26,9)[2][-1]
    h1=talib.MACD(np.append(c,x1),12,26,9)[2][-1]
    slope=(h1-h0)/(x1-x0)
    boundary=x0-h0/slope if slope>0 else None
    return {"status":"conditional_close","price":number(boundary) if boundary is not None and boundary>0 else None,
      "condition":"下一根周K最终收盘严格高于门槛；盘中越过不算确认","prior_closed_at":rows[-1]["close_time"],
      "method":"TA-Lib MACD(12,26,9) next-close affine threshold"}


def level_role(kind,low,high,bars):
    """A broken level is unavailable until a later closed retest confirms its new role."""
    role=kind;status='confirmed';events=[];break_time=None
    for b in bars:
        c,h,l=map(lambda k:D(str(b[k])),('close','high','low'))
        known=max(b['close_time'],b.get('received_at',b['close_time']))
        if status=='confirmed':
            broken=(role=='resistance' and c>high) or (role=='support' and c<low)
            if broken:
                status='role_pending';break_time=b['close_time'];events.append({'event':'broken','known_at':known,'close':str(c),'from_role':role})
        elif b['close_time']>break_time:
            reclaimed=(role=='resistance' and l<=high and h>=low and c>high) or (role=='support' and h>=low and l<=high and c<low)
            failed=(role=='resistance' and c<low) or (role=='support' and c>high)
            if reclaimed:
                role='support' if role=='resistance' else 'resistance';status='confirmed';events.append({'event':'retest_confirmed','known_at':known,'close':str(c),'new_role':role})
            elif failed:
                status='invalidated';events.append({'event':'failed_retest','known_at':known,'close':str(c)});break
    return {'kind':role,'status':status,'events':events}
