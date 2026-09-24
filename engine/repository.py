import time
from decimal import Decimal
from sqlalchemy import select, func, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from engine.db import Session
from engine.models import *
from engine.contracts import RuntimeConfig, MarketDTO, MarketList, Quality, OiChange
from engine.adapters.base import now_ms
from engine.features.oi import quantity_change

SOURCE_CAPABILITIES = {
    "binance":{"kind":"native","enabled":True,"scope":"dynamic_volume_universe","features":["catalog","candles","quantity_oi","settled_funding"]},
    "hyperliquid":{"kind":"native","enabled":True,"namespace":"default","features":["catalog","candles","quantity_oi","settled_funding"]},
    "aster":{"kind":"native","enabled":True,"scope":"all_eligible_rotating","features":["catalog","candles","quantity_oi","settled_funding"]},
    "cmc":{"kind":"supplement","enabled":True,"features":["valuation","derivatives","liquidations","shared_budget"]},
    "wallet_rising":{"kind":"discovery","enabled":True,"features":["rising_catalog","pool_ohlcv"]},
    "binance_spot":{"kind":"native","enabled":True,"features":["catalog","candles","spot_price"]},
    "binance_alpha":{"kind":"native","enabled":True,"features":["catalog","candles","chain_identity"]},
    "okx":{"kind":"native","enabled":True,"scope":"three_asset_pilot","features":["catalog","candles","quantity_oi","settled_funding"]},
}
def emit(session,kind,payload):
    # Serialize ID allocation with commit order across participating writers.
    if session.bind.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(1877101)"))
    event=StreamEvent(kind=kind,created_at=now_ms(),payload=payload)
    session.add(event);session.flush()
    return event.id

def bootstrap():
    with Session.begin() as s:
        s.execute(text("SELECT pg_advisory_xact_lock(1877102)"))
        if not s.scalar(select(SettingsVersion.id).limit(1)):
            s.add(SettingsVersion(state="published",config=RuntimeConfig().model_dump(),created_at=now_ms(),published_at=now_ms(),parent_version=None))
        else:
            current=active_config(s)
            if current and ("universe_mode" not in current.config or "analysis" not in current.config):
                # Preserve every historical version; upgrade only the active schema once.
                s.add(SettingsVersion(state="published",config=RuntimeConfig.model_validate(current.config).model_dump(),created_at=now_ms(),published_at=now_ms(),parent_version=current.id))
        for source,capabilities in SOURCE_CAPABILITIES.items():
            if not s.get(SourceHealth,source):
                s.add(SourceHealth(source=source,state="pending" if capabilities["enabled"] else "disabled",checked_at=now_ms(),last_success_at=None,message="等待首次采集" if capabilities["enabled"] else capabilities["reason"],capabilities=capabilities))
            else:
                row=s.get(SourceHealth,source);row.capabilities={**row.capabilities,**capabilities}
                if row.state=="disabled" and capabilities["enabled"]:row.state="pending";row.message="适配器已实现，等待当前进程首次采集"

def active_config(s):
    return s.scalar(select(SettingsVersion).where(SettingsVersion.state=="published").order_by(SettingsVersion.id.desc()).limit(1))

def runtime_config():
    with Session() as s:
        row=active_config(s)
        return RuntimeConfig.model_validate(row.config) if row else RuntimeConfig()

def heartbeat(role,state,details=None):
    with Session.begin() as s:
        s.merge(WorkerHeartbeat(role=role,updated_at=now_ms(),state=state,details=details or {}))

def save_failure(source,error_type):
    with Session.begin() as s:
        row=s.get(SourceHealth,source)
        row.state="error";row.checked_at=now_ms();row.message="公共接口暂不可用："+error_type
        emit(s,"source_update",{"source":source})

def save_batch(batch):
    with Session.begin() as s:
        for raw in batch.raw: s.merge(RawEvent(**raw))
        s.flush()
        for item in batch.instruments:
            s.execute(insert(Asset).values(id=item["asset_id"],symbol=item.get("spec",{}).get("display_symbol",item.get("spec",{}).get("base_asset",item["asset_id"]))[:40],identity_status="native_catalog",known_at=now_ms()).on_conflict_do_nothing(index_elements=["id"]))
            s.merge(Instrument(**item))
        s.flush()
        candle_keys=[(r["instrument_id"],r["resolution"],r["open_time"]) for r in batch.candles]
        existing={}
        if candle_keys:
            for start in range(0,len(candle_keys),300):
                existing.update({(c.instrument_id,c.resolution,c.open_time):c for c in s.scalars(select(Candle).where(tuple_(Candle.instrument_id,Candle.resolution,Candle.open_time).in_(candle_keys[start:start+300])))})
        candle_updates=[]
        for item in batch.candles:
            low,high=Decimal(item["low"]),Decimal(item["high"])
            if low>high or not low<=Decimal(item["open"])<=high or not low<=Decimal(item["close"])<=high: continue
            key=(item["instrument_id"],item["resolution"],item["open_time"])
            old=existing.get(key)
            revision=old.revision if old else 1
            if old and old.is_closed and any(getattr(old,k)!=(Decimal(item[k]) if item[k] is not None else None) for k in ("open","high","low","close","base_volume")):
                revision+=1
                emit(s,"candle_revision",{"instrument_id":item["instrument_id"],"resolution":item["resolution"],"open_time":item["open_time"],"revision":revision,"previous_raw_ref":old.raw_ref,"raw_ref":item["raw_ref"]})
            if old and old.is_closed and not item["is_closed"]: continue
            candle_updates.append({**item,"revision":revision})
        for offset in range(0,len(candle_updates),500):
            query=insert(Candle).values(candle_updates[offset:offset+500])
            s.execute(query.on_conflict_do_update(index_elements=["instrument_id","resolution","open_time"],set_={c.name:getattr(query.excluded,c.name) for c in Candle.__table__.columns if not c.primary_key}))
        for item in batch.oi: s.merge(OiPoint(**item))
        for item in batch.funding: s.merge(FundingPoint(**item))
        for item in batch.states:
            old=s.get(MarketState,item["instrument_id"])
            if old and old.as_of>item["as_of"]: continue
            s.merge(MarketState(instrument_id=item["instrument_id"],as_of=item["as_of"],payload={**(old.payload if old else {}),**item}))
        if batch.candle_only:
            emit(s,"candle_update",{"source":batch.venue,"count":len(candle_updates)})
            return
        health=s.get(SourceHealth,batch.venue)
        health.state="partial" if batch.errors else "ok"
        health.checked_at=now_ms()
        health.last_success_at=now_ms() if batch.states else health.last_success_at
        health.message=f"本次更新 {len(batch.states)} 个市场" + (f"；{len(batch.errors)} 项采集缺口" if batch.errors else "")
        health.capabilities={**SOURCE_CAPABILITIES[batch.venue],"latest_gaps":batch.errors,**batch.metadata}
        emit(s,"market_update",{"source":batch.venue,"instruments":[x["instrument_id"] for x in batch.states]})

def list_markets():
    now=now_ms();result=[]
    config=runtime_config();max_age=max(180_000,config.collector_interval_seconds*3*1000)
    with Session() as s:
        health=s.get(SourceHealth,"binance")
        universe=health.capabilities if health else {}
        selected_ids=set(universe.get("selected_ids",[]))
        counts=dict(s.execute(select(Candle.instrument_id,func.count()).where(Candle.resolution=="240").group_by(Candle.instrument_id)).all())
        # Batch the baselines instead of 3 SQL queries for each market.
        states=list(s.execute(select(Instrument,MarketState).join(MarketState,Instrument.id==MarketState.instrument_id).order_by(Instrument.asset_id,Instrument.venue)))
        ends={inst.id:st.payload.get("oi_as_of") for inst,st in states}
        baselines={}
        for period,duration in [("1H",3600_000),("4H",14400_000),("24H",86400_000)]:
            pairs=[(iid,end-duration) for iid,end in ends.items() if end]
            if not pairs: continue
            from sqlalchemy import values, column, String, BigInteger
            targets=values(column("iid",String),column("target",BigInteger),name="targets").data(pairs)
            nearest=(select(OiPoint.instrument_id,OiPoint.event_time,OiPoint.base_quantity,OiPoint.mark_price,OiPoint.notional_quote)
                .join(targets,OiPoint.instrument_id==targets.c.iid).where(OiPoint.event_time<=targets.c.target,OiPoint.event_time>=targets.c.target-max_age)
                .distinct(OiPoint.instrument_id).order_by(OiPoint.instrument_id,OiPoint.event_time.desc()))
            baselines[period]={r.instrument_id:r for r in s.execute(nearest)}
        for inst,state in states:
            if inst.spec.get("crypto_eligible") is False:continue
            p=state.payload;fresh=0<=now-state.as_of<=max_age
            changes=[]
            oi_fresh=p.get("oi_status")=="fresh" and 0<=now-p.get("oi_as_of",0)<=max_age
            for period,label,duration in [("1H","1H",3600_000),("4H","4H",14400_000),("24H","1D",86400_000)]:
                end=p.get("oi_as_of");start=None;value=None;reason="insufficient_history";extra={}
                if not oi_fresh: reason="stale" if p.get("oi_as_of") else "not_applicable" if inst.market_type in ("spot","dex_pool") else "missing"
                elif end:
                    target=end-duration
                    old=baselines.get(period,{}).get(inst.id)
                    if old and target-old.event_time<=max_age:
                        start=old.event_time
                        value,reason=quantity_change(old.base_quantity,Decimal(p["oi_base"]),{inst.id},{inst.id})
                        if reason=="fresh":
                            from engine.features.observations import paired_changes
                            extra=paired_changes(old.base_quantity,old.mark_price,Decimal(p["oi_base"]),Decimal(p["oi_notional_quote"]),inst.quote_asset)
                changes.append(OiChange(period=period,display_label=label,value_pct=value,quality=Quality(status=reason),start_sample_time=start,end_sample_time=end,**extra))
            rate_time=p.get("funding_settled_at");interval=p.get("funding_interval_seconds")
            funding_ok=rate_time is not None and interval is not None and 0<=now-rate_time<=(interval+300)*1000
            count=counts.get(inst.id,0)
            result.append(MarketDTO(instrument_id=inst.id,asset_id=inst.asset_id,symbol=inst.spec.get("display_symbol",inst.spec.get("base_asset",inst.asset_id)),venue=inst.venue,native_symbol=inst.native_symbol,quote_asset=inst.quote_asset,price_kind=p["price_kind"],price=p.get("price"),as_of=state.as_of,
                quality=Quality(status="fresh" if fresh else "stale",reason=None if fresh else "报价已过期"),
                oi_base=p.get("oi_base"),oi_notional_quote=p.get("oi_notional_quote"),oi_quote_asset=p["oi_quote_asset"],oi_changes=changes,oi_as_of=p.get("oi_as_of"),
                oi_quality=Quality(status="fresh" if oi_fresh else ("stale" if p.get("oi_as_of") else "not_applicable" if inst.market_type in ("spot","dex_pool") else "missing")),
                funding_rate=p.get("funding_rate"),funding_kind="settled",funding_interval_seconds=interval,funding_settled_at=rate_time,
                funding_quality=Quality(status="fresh" if funding_ok else ("stale" if rate_time else "not_applicable" if inst.market_type in ("spot","dex_pool") else "missing")),candles_count=count,
                volume_24h_quote=p.get("volume_24h_quote"),universe_rank=p.get("universe_rank"),
                in_universe=(inst.id in selected_ids if inst.venue=="binance" and selected_ids else True),
                deep_history=p.get("deep_history",inst.venue!="binance")))
    from engine.pd2.providers import valuation_for
    with Session() as valuation_session:
        for market in result:
            v=valuation_for(market,valuation_session,now)
            market.circulating_cap=v["cap"];market.oi_cap_ratio=v["ratio"];market.oi_notional_usd=v["usd"]
            market.valuation_status=v["status"];market.coverage=v
    return MarketList(as_of=now,rows=result,universe={k:v for k,v in universe.items() if k not in ("selected_ids","deep_ids","latest_gaps")})

def candle_targets():
    """Only the bounded deep pool receives history; resume from persisted candle watermarks."""
    config=runtime_config()
    with Session() as s:
        source=s.get(SourceHealth,"binance")
        eligible=(source.capabilities if source else {}).get("selected_ids",[])
        active=list(s.scalars(select(Campaign.reference_instrument_id).join(Episode).where(Episode.phase.notin_(["invalidated","ended"]))))
        watermarks=dict(s.execute(select(Candle.instrument_id,func.max(Candle.received_at)).where(Candle.resolution=="240").group_by(Candle.instrument_id)).all())
        background=sorted(eligible,key=lambda iid:(watermarks.get(iid,0),iid))
        selected=list(dict.fromkeys(active+background[:config.deep_asset_limit]))
        instruments=list(s.scalars(select(Instrument).where(Instrument.id.in_(selected),Instrument.venue=="binance")))
        latest={(i,r):t for i,r,t in s.execute(select(Candle.instrument_id,Candle.resolution,func.max(Candle.open_time)).where(Candle.instrument_id.in_(selected)).group_by(Candle.instrument_id,Candle.resolution))}
        now=now_ms();targets=[]
        rank={iid:n for n,iid in enumerate(selected)}
        for inst in sorted(instruments,key=lambda i:rank[i.id]):
            for resolution,interval,duration in [("60","1h",3600_000),("240","4h",14400_000),("1D","1d",86400_000),("1W","1w",604800_000)]:
                watermark=latest.get((inst.id,resolution))
                limit=300 if watermark is None else min(300,max(2,(now-watermark)//duration+2))
                targets.append((inst.id,inst.native_symbol,resolution,interval,limit))
        return targets

def read_events(after,limit=200):
    with Session() as s:
        return [{"id":r.id,"kind":r.kind,"created_at":r.created_at,"payload":r.payload} for r in s.scalars(select(StreamEvent).where(StreamEvent.id>after).order_by(StreamEvent.id).limit(limit))]
