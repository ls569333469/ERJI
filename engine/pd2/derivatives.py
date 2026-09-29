"""Provider facts retain market scope, timestamps and identity; never infer unseen positions."""
from collections import defaultdict
from decimal import Decimal as D
from hashlib import sha256
import json
from engine.pd2.providers import timestamp

def cmc_markets(document, asset_id, now, max_age=300000):
    data=document.get("data") or {};groups=defaultdict(list);excluded=[];accepted=[]
    for row in data.get("market_pairs",[]):
        key=(row.get("exchange",{}).get("exchange_id"),row.get("market_id"))
        groups[key].append(row)
    for key,items in groups.items():
        row=items[0];reason=None
        if None in key:reason="identity_missing"
        elif len({json.dumps(x,sort_keys=True) for x in items})>1:reason="duplicate_conflict"
        elif str(row.get("market_pair_base",{}).get("crypto_id"))!=str(asset_id):reason="identity_conflict"
        elif row.get("category")!="perpetual":reason="unsupported_contract"
        elif row.get("outlier_detected") or "open_interest" in row.get("exclusions",[]):reason="provider_excluded"
        quotes=[x for x in row.get("quotes",[]) if x.get("convert_symbol")=="USD"]
        if not quotes:reason=reason or "usd_oi_missing"
        q=quotes[0] if quotes else {};observed=timestamp(q.get("last_updated"))
        if q.get("open_interest") is None:reason=reason or "not_returned"
        if not observed or not 0<=now-observed<=max_age:reason=reason or "stale"
        if reason:excluded.append({"market_key":key,"reason":reason});continue
        oi=D(str(q["open_interest"]))
        if not oi.is_finite() or oi<0:excluded.append({"market_key":key,"reason":"invalid_number"});continue
        accepted.append({"market_key":str(key[0])+":"+str(key[1]),"venue":row["exchange"].get("exchange_slug"),
          "native_pair":row.get("market_pair_symbol"),"notional_usd":str(oi),"quantity":None,
          "quantity_status":"unsupported","observed_at":observed,"source":"cmc"})
    total=sum(D(v["notional_usd"]) for v in accepted)
    for v in accepted:v["share_pct"]=str(D(v["notional_usd"])/total*100) if total else None
    reported=data.get("num_market_pairs")
    return {"status":"fresh" if accepted else "unavailable","markets":accepted,"excluded":excluded,
      "total_oi_usd":str(total) if accepted else None,"coverage_hash":sha256(json.dumps(sorted(x["market_key"] for x in accepted)).encode()).hexdigest()[:16],
      "returned_rows":len(data.get("market_pairs",[])),"reported_rows":reported,
      "truncated":reported is not None and reported>len(data.get("market_pairs",[])),"scope":"provider_covered_markets_not_global"}

def model_map(document, symbol, native_symbol, mark, now, max_age=300000):
    observed=timestamp(document.get("as_of"));meta=document.get("meta") or {}
    # Response generation can be recent while the underlying price snapshots are old.
    age=meta.get("max_stale_age_ms")
    status="fresh" if observed and 0<=now-observed<=max_age and (age is None or age<=max_age) else "stale"
    if document.get("symbol")!=symbol or document.get("exchange_symbol")!=native_symbol:status="identity_conflict"
    reference=(document.get("real_levels") or {}).get("reference_price")
    if reference is None:reference=(document.get("stats") or {}).get("last_price")
    levels=[];events=[]
    for i,row in enumerate((document.get("real_levels") or {}).get("levels",[])):
        price=row.get("price");side=row.get("side")
        if price is None or D(str(price))<=0 or side not in ("long","short"):continue
        levels.append({"id":f"model:{symbol}:{i}","price":str(price),"liquidated_side":side,
          "notional_usd":None if row.get("notional_usd") is None else str(row["notional_usd"]),
          "distance_pct":str((D(str(price))/D(str(reference))-1)*100) if reference and D(str(reference))>0 else None,
          "reference_price":str(reference) if reference is not None else None,
          "kind":"modeled","scope":"multi_exchange","status":status,"observed_at":observed,
          "price_comparability":"reference_only_cross_venue","action_eligible":False})
    for row in (document.get("real_liquidations") or {}).get("points",[]):
        at=timestamp(row.get("time"))
        if not at or at>now or row.get("side") not in ("long","short"):continue
        key=sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()[:24]
        events.append({"id":key,"time":at,"price":str(row.get("price")),"notional_usd":row.get("notional_usd"),
          "liquidated_side":row["side"],"kind":"recorded_sample","source":"bykaranteli","scope":"multi_exchange_sample"})
    events=list({e["id"]:e for e in events}.values())
    return {"status":status,"source":"ByKaranteli","kind":"modeled","levels":levels,"recorded_events":events,
      "observed_at":observed,"underlying_max_age_ms":age,"methodology":document.get("methodology"),
      "reference_price":str(reference) if reference is not None else None,
      "heatmap":{"x_labels":document.get("x_labels",[]),"band_prices":document.get("band_prices",[]),"cells":document.get("heatmap_cells",[]),"price_path":document.get("price_path",[])},
      "empty_events_mean":"not_observed_not_zero","price_basis":"aggregated_model_reference","extra_upside_forecast":None}

def whale_positions(document, coin, now):
    groups=defaultdict(list);result=[];quarantined=[]
    for p in document.get("positions",[]):
        if p.get("coin")==coin:groups[(str(p.get("address","")).lower(),coin)].append(p)
    for (address,name),rows in groups.items():
        if not address or len({json.dumps(x,sort_keys=True) for x in rows})>1:
            quarantined.append({"coin":coin,"reason":"identity_conflict"});continue
        row=rows[0];price=row.get("liq_px")
        at=timestamp(row.get("as_of") or row.get("updated_at"))
        result.append({"id":sha256((address+":"+name).encode()).hexdigest()[:24],"coin":coin,
          "kind":"reported" if price is not None else "unavailable","liquidation_price":str(price) if price is not None else None,
          "side":"short" if D(str(row.get("size",0)))<0 else "long","notional_usd":row.get("notional_usd"),
          "status":"fresh" if at and 0<=now-at<=300000 else "source_time_unknown" if not at else "stale",
          "observed_at":at,"scope":"hyperliquid_public_account_sample","namespace":"default"})
    return {"status":"sample_available" if result else "out_of_sample","positions":result,"excluded":quarantined,
      "accounts_scanned":document.get("accounts_scanned"),"sample_note":document.get("note"),"scope":"sample_not_entire_exchange"}

def settled_costs(points, now, observed_through=None, max_age=180000):
    # A complete latest-history response proves the tail; a settlement interval
    # cannot prove that a source has not shortened its next settlement cycle.
    grouped=defaultdict(list)
    for row in points:
        if row["time"]<=now:grouped[row["time"]].append(row)
    conflicts={t for t,values in grouped.items() if len({(str(v["rate"]),v.get("interval_seconds")) for v in values})>1}
    rows=sorted((v[0] for v in grouped.values()),key=lambda r:r["time"])
    end=observed_through if observed_through is not None and observed_through<=now else now
    # Without a collection checkpoint only a settlement exactly at now closes the tail.
    tail_known=(observed_through is not None and 0<=now-observed_through<=max_age) or bool(rows and rows[-1]["time"]==now)
    result=[]
    for label,duration in (("1H",3600000),("4H",14400000),("1D",86400000)):
        start=end-duration;chosen=[r for r in rows if start<r["time"]<=end]
        prior=next((r for r in reversed(rows) if r["time"]<=start),None)
        chain=([prior] if prior else [])+chosen;reason=None
        if any(t in conflicts for t in [r["time"] for r in chain]):reason="conflicting_settlement"
        elif not rows:reason="insufficient_history"
        elif not tail_known:reason="tail_unverified"
        elif not prior and (not chosen or not chosen[0].get("interval_seconds") or chosen[0]["time"]-chosen[0]["interval_seconds"]*1000>start):reason="insufficient_history"
        elif any(not r.get("interval_seconds") for r in chosen):reason="interval_unknown"
        elif any(b["time"]-b["interval_seconds"]*1000!=a["time"] for a,b in zip(chain,chain[1:])):reason="gap"
        total=sum((D(str(r["rate"])) for r in chosen),D(0)) if not reason else None
        result.append({"period":label,"status":reason or "available","settlement_count":len(chosen),
          "start_at":start,"end_at":end,"observed_through":observed_through,
          "settled_rate_sum":str(total) if total is not None else None,"kind":"actual_settlement_cashflows_in_window",
          "meaning":"正数表示该窗口持多支付、持空收取；不含未结算预测；零值仅在完整采集窗口内成立"})
    return result

def oi_daily_baseline(points, current):
    by_day={}
    for p in sorted(points,key=lambda r:r["time"]):
        if p.get("quantity") is not None:by_day[p["time"]//86400000]=D(str(p["quantity"]))
    values=list(by_day.values())
    if current is None or len(values)<30:return {"status":"insufficient_history","independent_days":len(values),"percentile":None}
    q=D(str(current));rank=sum(v<q for v in values)+D(".5")*sum(v==q for v in values)
    return {"status":"available","independent_days":len(values),"percentile":str(rank/len(values)*100),"method":"one_last_sample_per_UTC_day"}


def cmc_liquidations(document,asset_id,now,max_age=300000):
    rows=[r for r in (document.get('data') or {}).get('cryptocurrencies',[]) if str(r.get('crypto_id'))==str(asset_id)]
    if not rows:return {'status':'not_returned','windows':[],'source':'CoinMarketCap','scope':'provider_covered_venues'}
    if len(rows)!=1:return {'status':'identity_conflict','windows':[]}
    quotes=[q for q in rows[0].get('quotes',[]) if q.get('symbol')=='USD']
    if len(quotes)!=1:return {'status':'not_returned','windows':[]}
    q=quotes[0];at=timestamp(q.get('last_updated'));fresh=at is not None and 0<=now-at<=max_age
    result=[]
    for label,suffix in [('1H','1h'),('4H','4h'),('1D','24h')]:
        short=q.get('short_liquidations_'+suffix);long=q.get('long_liquidations_'+suffix)
        result.append({'period':label,'short_usd':str(short) if short is not None else None,'long_usd':str(long) if long is not None else None,'status':'fresh' if fresh and short is not None and long is not None else 'stale' if not fresh else 'not_returned','kind':'reported_window_summary'})
    return {'status':'fresh' if fresh else 'stale','observed_at':at,'windows':result,'source':'CoinMarketCap','scope':'provider_covered_venues','add_to_native':False}


def context_for(market, now):
    from sqlalchemy import select
    from engine.db import Session
    from engine.models import IdentityLink,FundingPoint,OiPoint,Instrument,MarketState,UserPreference,RawEvent
    from engine.pd2.storage import get
    with Session() as s:
        link=s.get(IdentityLink,(market.asset_id,"cmc"))
        cmc=get("cmc","derivatives",link.external_id) if link and link.status=="verified" else {"status":"unmapped"}
        fund=[{"time":r.settled_at,"rate":str(r.rate),"interval_seconds":r.interval_seconds} for r in s.scalars(select(FundingPoint).where(FundingPoint.instrument_id==market.instrument_id,FundingPoint.settled_at<=now,FundingPoint.settled_at>=now-2*86400000).order_by(FundingPoint.settled_at))]
        samples=[{"time":r.event_time,"quantity":str(r.base_quantity) if r.base_quantity is not None else None} for r in s.scalars(select(OiPoint).where(OiPoint.instrument_id==market.instrument_id,OiPoint.event_time<=now,OiPoint.received_at<=now,OiPoint.event_time>=now-90*86400000))]
    symbol=market.native_symbol.removesuffix(market.quote_asset) if market.venue!="hyperliquid" else market.native_symbol
    mapped=get("bykaranteli","liqmap",symbol)
    whales=get("bykaranteli","whales","global")
    cross=cmc_markets(cmc["payload"],link.external_id,now) if cmc.get("payload") and cmc["status"]=="fresh" else {"status":cmc["status"],"markets":[],"total_oi_usd":None}
    from engine.pd2.oi_aggregate import aggregate_oi
    from engine.pd2.providers import quote_usd_rate
    native=[]
    with Session() as s:
        for inst,st in s.execute(select(Instrument,MarketState).join(MarketState).where(Instrument.asset_id==market.asset_id)):
            p=st.payload;fx=quote_usd_rate(inst.quote_asset,s,now);oi=p.get("oi_notional_quote");at=p.get("oi_as_of")
            if oi is None or not fx or at is None:continue
            native.append({"instrument_id":inst.id,"venue":inst.venue,"native_pair":inst.spec.get("base_asset",inst.native_symbol.removesuffix(inst.quote_asset))+"/"+inst.quote_asset,"notional_usd":str(D(str(oi))*fx),"quantity":p.get("oi_base"),"observed_at":at,"quantity_status":"native_base_quantity"})
        selection=s.get(UserPreference,"main_market:"+market.asset_id)
        main=(selection.payload if selection else {}).get("selected_perpetual")
        if main and main!=market.instrument_id:
            fund=[{"time":r.settled_at,"rate":str(r.rate),"interval_seconds":r.interval_seconds} for r in s.scalars(select(FundingPoint).where(FundingPoint.instrument_id==main,FundingPoint.settled_at<=now,FundingPoint.settled_at>=now-2*86400000).order_by(FundingPoint.settled_at))]
    cross=aggregate_oi(native,cross,now)
    from engine.pd2.oi_aggregate import history_changes
    cross["changes"]=history_changes(market.asset_id,cross,now)
    cap=market.circulating_cap
    cross["oi_cap_ratio"]=str(D(cross["total_oi_usd"])/cap) if cross.get("total_oi_usd") is not None and cap and cap>0 else None
    cross["native_not_added"]=False
    model=model_map(mapped["payload"],symbol,market.native_symbol,market.price,now) if mapped.get("payload") else {"status":mapped["status"],"levels":[],"recorded_events":[]}
    positions=whale_positions(whales["payload"],market.native_symbol,now) if market.venue=="hyperliquid" and whales.get("payload") else {"status":"out_of_sample","positions":[]}
    from engine.pd2.native_streams import tape_context
    tape=tape_context(market.instrument_id,now)
    cmc_liq=get("cmc","liquidations","global")
    liquidation_summary=cmc_liquidations(cmc_liq["payload"],link.external_id,now) if link and cmc_liq.get("payload") else {"status":"unmapped" if not link else "not_collected","windows":[]}
    with Session() as s:
        checkpoint=s.scalar(select(RawEvent.received_at).join(FundingPoint,FundingPoint.raw_ref==RawEvent.id).where(FundingPoint.instrument_id==(main or market.instrument_id),RawEvent.received_at<=now).order_by(RawEvent.received_at.desc()).limit(1))
    costs=settled_costs(fund,now,checkpoint)
    facts=[]
    day=next((x for x in market.oi_changes if x.period=="24H"),None)
    if day and day.value_pct is not None:
        facts.append({"kind":"quantity_change","text":f"1D数量持仓变化 {day.value_pct:.2f}%；金额变化不能替代数量变化","action_impact":"确认参与仍以结构、现价和净R为准"})
    for window in liquidation_summary.get("windows",[]):
        if window["period"]=="1H" and window["status"]=="fresh":
            facts.append({"kind":"reported_liquidations","text":f'近1H CMC已报告空头清算 {window["short_usd"]} USD、多头清算 {window["long_usd"]} USD',"action_impact":"已发生清算证据；不与原生采样相加，不推算剩余可爆仓总量"})
    recent=[e for e in model.get("recorded_events",[]) if now-3600000<=e["time"]<=now]
    if recent:
        short=sum(D(str(e.get("notional_usd") or 0)) for e in recent if e["liquidated_side"]=="short")
        long=sum(D(str(e.get("notional_usd") or 0)) for e in recent if e["liquidated_side"]=="long")
        facts.append({"kind":"recorded_liquidations","text":f"近1H来源样本：空头清算 {short:.0f} USD，多头清算 {long:.0f} USD","action_impact":"已发生清算证据，不代表未来买盘或全市场总量"})
    return {"cross_market_oi":cross,"funding_reference_instrument":main or market.instrument_id,"settled_costs":costs,"oi_baseline":oi_daily_baseline(samples,market.oi_base),
      "liquidation_model":model,"visible_positions":positions,"liquidation_summary":liquidation_summary,"native_tape":tape,"facts":facts,"as_of":now}
