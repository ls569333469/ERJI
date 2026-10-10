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
        elif any(abs(b["time"]-b["interval_seconds"]*1000-a["time"])>1000 for a,b in zip(chain,chain[1:])):reason="gap"
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


def shared_inputs(now):
    """Load batch-wide identity, FX and global liquidation inputs once."""
    from engine.db import Session
    from engine.pd2.universe_policy import membership
    from engine.pd2.providers import quote_usd_rate

    with Session() as session:
        aliases, _ = membership(session)
        fx_rates = {quote: quote_usd_rate(quote, session, now) for quote in ("USD", "USDT", "USDC")}
    members = {}
    for asset, canonical in aliases.items():
        members.setdefault(canonical, []).append(asset)
    return {
        "aliases": aliases,
        "members": members,
        "fx_rates": fx_rates,
    }


def choose_reference(catalog, selected=None):
    """Verified catalog identity is independent of FX/OI availability."""
    if not catalog: return None
    primary=[c for c in catalog if c['venue']=='binance']
    pool=primary
    if not pool:return None
    return next((c for c in pool if c['instrument_id']==selected),None) or min(
        pool,key=lambda c:(c.get('quote_asset')!='USDT',c['instrument_id']))


def context_for(market, now, shared=None, compact=False):
    from sqlalchemy import select
    from engine.db import Session
    from engine.models import FundingPoint,OiPoint,Instrument,MarketState,RawEvent
    shared = shared or shared_inputs(now)
    cross={"status":"unavailable","markets":[],"total_oi_usd":None}
    from engine.pd2.oi_aggregate import aggregate_oi
    native=[];perpetual_candidates=[]
    with Session() as s:
        aliases=shared["aliases"]
        canonical = aliases.get(market.asset_id, market.asset_id)
        matched = shared["members"].get(canonical,[market.asset_id])
        for inst,st in s.execute(select(Instrument,MarketState).outerjoin(MarketState).where(Instrument.asset_id.in_(matched))):
            if inst.venue!='binance':continue
            # Reference facts are not pool admission: an existing USD contract
            # can provide OI without extending the USDT/USDC collection pool.
            if (inst.market_type not in ('linear_perpetual','perpetual')
                    or (inst.spec or {}).get('crypto_eligible') is False
                    or (inst.spec or {}).get('status','TRADING') not in ('TRADING','active')):continue
            p=st.payload if st else {};fx=shared["fx_rates"].get(inst.quote_asset);oi=p.get("oi_notional_quote");at=p.get("oi_as_of")
            if inst.market_type in ('linear_perpetual','perpetual') and inst.spec.get('status') not in ('CLOSED','DELISTED','inactive','delisted'):
                perpetual_candidates.append({'instrument_id':inst.id,'venue':inst.venue,'quote_asset':inst.quote_asset,
                    'fresh_oi':p.get('oi_status')=='fresh' and at is not None and 0<=now-at<=360000 and oi is not None,
                    'fx_status':'available' if fx else 'missing_or_stale'})
            if oi is None or not fx or at is None or p.get('oi_status')!='fresh':continue
            notional_usd=D(str(oi))*fx
            native.append({"instrument_id":inst.id,"venue":inst.venue,"native_pair":inst.spec.get("base_asset",inst.native_symbol.removesuffix(inst.quote_asset))+"/"+inst.quote_asset,"notional_usd":str(notional_usd),"quantity":p.get("oi_base"),"observed_at":at,"quantity_status":"native_base_quantity"})
        chosen=choose_reference(perpetual_candidates)
        main=chosen['instrument_id'] if chosen else None
        reference_selection='verified_binance_catalog' if chosen else 'no_verified_contract'
        reference=main or market.instrument_id
        fund=[] if compact else [{"time":r.settled_at,"rate":str(r.rate),"interval_seconds":r.interval_seconds} for r in s.scalars(select(FundingPoint).where(FundingPoint.instrument_id==reference,FundingPoint.settled_at<=now,FundingPoint.settled_at>=now-2*86400000).order_by(FundingPoint.settled_at))]
        samples=[] if compact else [{"time":r.event_time,"quantity":str(r.base_quantity) if r.base_quantity is not None else None} for r in s.scalars(select(OiPoint).where(OiPoint.instrument_id==reference,OiPoint.event_time<=now,OiPoint.received_at<=now,OiPoint.event_time>=now-90*86400000))]
        funding_row=s.get(MarketState,reference)
        funding_payload=funding_row.payload if funding_row else {}
    native_reference={'instrument_id':main,'status':'unmapped' if not chosen else 'fresh' if chosen['fresh_oi'] else 'stale' if funding_payload.get('oi_as_of') else 'not_collected',
        'quantity':funding_payload.get('oi_base') if main else None,'notional_quote':funding_payload.get('oi_notional_quote') if main else None,
        'quote_asset':chosen['quote_asset'] if chosen else None,'observed_at':funding_payload.get('oi_as_of') if main else None,
        'fx_status':chosen['fx_status'] if chosen else None}
    reference_fact={'instrument_id':main,'selection':reference_selection if main else 'no_verified_contract',
        'contract_status':'verified' if main else 'unmapped','observed_at':funding_payload.get('oi_as_of')}
    settled_at=funding_payload.get('funding_settled_at');interval=funding_payload.get('funding_interval_seconds')
    funding_fact={'instrument_id':main,'rate':funding_payload.get('funding_rate') if main else None,
        'interval_seconds':interval,'settled_at':settled_at,'selection':reference_selection,
        'status':'available' if main and settled_at and interval and 0<=now-settled_at<=interval*1000+180000 else 'stale' if settled_at else 'not_collected'}
    cross=aggregate_oi(native,cross,now)
    # The primary OI ratio is the verified Binance reference contract, not a
    # changing mixture of fresh auxiliary venues or alternate quote contracts.
    cross['supplemental_markets']=cross['markets']
    if chosen and chosen['venue']=='binance':
        primary=[r for r in native if r['instrument_id']==main]
        primary_total=aggregate_oi(primary,{'markets':[]},now)
        cross.update(primary_total,scope='binance_reference_contract',reference_instrument_id=main)
        if not primary_total['markets']:
            cross.update(status='primary_stale' if funding_payload.get('oi_as_of') else 'unavailable')
    from engine.pd2.oi_aggregate import history_changes
    cross["changes"]=history_changes(market.asset_id,cross,now)
    cap=market.circulating_cap
    cross["oi_cap_ratio"]=str(D(cross["total_oi_usd"])/cap) if cross.get("total_oi_usd") is not None and cap and cap>0 else None
    cross["native_not_added"]=False
    if compact:
        # The list needs OI and funding, not 24h of every trade or 90d of OI.
        # Full evidence remains available to immutable decision snapshots.
        return {"cross_market_oi":cross,"native_reference":native_reference,
          "derivatives_reference_market":reference_fact,
          "funding_reference":funding_fact,
          "as_of":now}
    # Retired fields remain in the contract for frozen historical consumers.
    references={}
    model={"status":"disabled_by_policy","levels":[],"recorded_events":[]}
    positions={"status":"disabled_by_policy","positions":[]}
    liquidation_summary={"status":"disabled_by_policy","windows":[]}
    from engine.pd2.native_streams import tape_context
    tape=tape_context(market.instrument_id,now)
    with Session() as s:
        checkpoint=s.scalar(select(RawEvent.received_at).join(FundingPoint,FundingPoint.raw_ref==RawEvent.id).where(FundingPoint.instrument_id==reference,RawEvent.received_at<=now).order_by(RawEvent.received_at.desc()).limit(1))
    costs=settled_costs(fund,now,checkpoint)
    facts=[]
    day=next((x for x in market.oi_changes if x.period=="24H"),None)
    if day and day.value_pct is not None:
        facts.append({"kind":"quantity_change","text":f"1D数量持仓变化 {day.value_pct:.2f}%；金额变化不能替代数量变化","action_impact":"参与仍以策略、结构、现价和数据有效性为准；费用与盈亏比仅参考"})
    reference_quantity=funding_payload.get('oi_base')
    return {"cross_market_oi":cross,"native_reference":native_reference,"funding_reference_instrument":main,
      "derivatives_reference_market":reference_fact,
      "supplemental_references":references,"funding_reference":funding_fact,"settled_costs":costs,"oi_baseline":oi_daily_baseline(samples,reference_quantity),
      "liquidation_model":model,"visible_positions":positions,"liquidation_summary":liquidation_summary,"native_tape":tape,"facts":facts,"as_of":now}
