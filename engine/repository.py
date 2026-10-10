import time
from decimal import Decimal
from sqlalchemy import select, func, text, tuple_, or_, and_, case, literal_column
from sqlalchemy.dialects.postgresql import insert
from engine.db import Session
from engine.models import *
from engine.contracts import RuntimeConfig, MarketDTO, MarketList, Quality, OiChange
from engine.adapters.base import now_ms
from engine.features.oi import quantity_change
from engine.db_retry import retry_transaction

SOURCE_CAPABILITIES = {
    "binance":{"kind":"native","enabled":True,"scope":"dynamic_volume_universe","features":["catalog","candles","quantity_oi","settled_funding"]},
    "wallet_rising":{"kind":"discovery","enabled":True,"features":["rising_catalog"],"coverage_limit":"链上发现保留；行情与K线仅接入已核验的币安原生市场"},
    "binance_spot":{"kind":"native","enabled":True,"features":["catalog","candles","spot_price"]},
    "binance_alpha":{"kind":"native","enabled":True,"features":["catalog","candles","chain_identity"]},
}
from engine.pd2.source_policy import DISABLED,REALTIME,REASON
for _source in DISABLED:
    SOURCE_CAPABILITIES[_source]={"kind":"historical","enabled":False,"features":[],"reason":REASON}

def emit(session,kind,payload):
    # A multi-market scan can still need nested read sessions. Hold the global
    # event-ID lock only when its calculations have finished, immediately before commit.
    if kind=='signal_update' and 'deferred_signal_events' in session.info:
        session.info['deferred_signal_events'].append(payload)
        return None
    # A receipt gets its ID at commit. Callers may continue business writes;
    # neither an ORM autoflush nor a later lease guard can invert this lock.
    from engine.transaction_events import queue_event
    return queue_event(session,kind,now_ms(),payload)

def bootstrap():
    with Session.begin() as s:
        s.execute(text("SELECT pg_advisory_xact_lock(1877102)"))
        from engine.pd2.binance_identity import seed
        seed(s)
        from engine.pd2.source_policy import retire_runtime
        retire_runtime(s,now_ms())
        if not s.scalar(select(SettingsVersion.id).limit(1)):
            s.add(SettingsVersion(state="published",config=RuntimeConfig().model_dump(),created_at=now_ms(),published_at=now_ms(),parent_version=None))
        else:
            current=active_config(s)
            if current and ("universe_mode" not in current.config or "analysis" not in current.config or current.config.get("sources",{}).get("policy")!="binance_only"):
                # Preserve every historical version; upgrade only the active schema once.
                s.add(SettingsVersion(state="published",config=RuntimeConfig.model_validate(current.config).model_dump(),created_at=now_ms(),published_at=now_ms(),parent_version=current.id))
        for source,capabilities in SOURCE_CAPABILITIES.items():
            if not s.get(SourceHealth,source):
                s.add(SourceHealth(source=source,state="pending" if capabilities["enabled"] else "disabled",checked_at=now_ms(),last_success_at=None,message="等待首次采集" if capabilities["enabled"] else capabilities["reason"],capabilities=capabilities))
            else:
                row=s.get(SourceHealth,source);row.capabilities={**row.capabilities,**capabilities}
                if not capabilities["enabled"]:row.state="disabled";row.message=capabilities["reason"];row.checked_at=now_ms()
                if row.state=="disabled" and capabilities["enabled"]:row.state="pending";row.message="适配器已实现，等待当前进程首次采集"

def active_config(s):
    return s.scalar(select(SettingsVersion).where(SettingsVersion.state=="published").order_by(SettingsVersion.id.desc()).limit(1))

def current_config(value):
    from engine.p39.timing import current_execution
    return value.model_copy(update={'execution':current_execution(value.execution)})

def runtime_config(session=None):
    if session is not None:
        row=active_config(session)
        return current_config(RuntimeConfig.model_validate(row.config) if row else RuntimeConfig())
    with Session() as s:
        row=active_config(s)
        return current_config(RuntimeConfig.model_validate(row.config) if row else RuntimeConfig())

def write_heartbeat(session,role,at,state,details):
    import os
    details={**(details or {}),'run_id':os.getenv('ERJI_RUN_ID'),'pid':os.getpid()}
    # Two market computations can create a shared role concurrently. Merge's
    # SELECT-then-INSERT races on an initially absent row; UPSERT is atomic.
    stmt=insert(WorkerHeartbeat).values(role=role,updated_at=at,state=state,details=details)
    session.execute(stmt.on_conflict_do_update(index_elements=['role'],set_={
        'updated_at':stmt.excluded.updated_at,'state':stmt.excluded.state,'details':stmt.excluded.details},
        where=WorkerHeartbeat.updated_at<=stmt.excluded.updated_at))


@retry_transaction
def heartbeat(role,state,details=None):
    import os
    # Market-scoped role names can exceed the persisted 40-character key.
    # Preserve the full identity while avoiding truncation collisions.
    values=dict(details or {})
    if len(role)>40:
        from hashlib import sha256
        values['role_name']=role
        role=role[:23]+':'+sha256(role.encode()).hexdigest()[:16]
    with Session.begin() as s:
        write_heartbeat(s,role,now_ms(),state,{**values,'run_id':os.getenv('ERJI_RUN_ID'),'pid':os.getpid()})

def save_failure(source,error_type):
    with Session.begin() as s:
        row=s.get(SourceHealth,source)
        row.state="error";row.checked_at=now_ms();row.message="公共接口暂不可用："+error_type
        emit(s,"source_update",{"source":source})

def unique_candles(rows):
    """A PostgreSQL multi-row upsert must target each candle at most once."""
    unique={}
    for row in rows:
        key=(row['instrument_id'],row['resolution'],row['open_time'])
        previous=unique.get(key)
        if previous:
            if previous['is_closed'] and not row['is_closed']:continue
            if row['received_at']<previous['received_at']:continue
        unique[key]=row
    return list(unique.values())

def _bulk_merge(session, model, rows):
    """Preserve partial-update semantics without one SELECT per remote row."""
    keys=[c.name for c in model.__table__.primary_key.columns]
    unique={}
    for row in rows:
        key=tuple(row[k] for k in keys)
        unique[key]={**unique.get(key,{}),**row}
    groups={}
    for key in sorted(unique):
        row=unique[key]
        groups.setdefault(tuple(sorted(row)),[]).append(row)
    for columns,items in groups.items():
        for offset in range(0,len(items),100):
            query=insert(model).values(items[offset:offset+100])
            updates={k:getattr(query.excluded,k) for k in columns if k not in keys}
            query=(query.on_conflict_do_update(index_elements=keys,set_=updates) if updates
                   else query.on_conflict_do_nothing(index_elements=keys))
            session.execute(query)

@retry_transaction
def save_batch(batch):
    candles=unique_candles(batch.candles)
    with Session.begin() as s:
        # Raw IDs identify immutable evidence. Replays do not rewrite the original.
        raw=sorted({r['id']:r for r in batch.raw}.values(),key=lambda r:r['id'])
        for offset in range(0,len(raw),500):
            s.execute(insert(RawEvent).values(raw[offset:offset+500]).on_conflict_do_nothing(index_elements=['id']))
        assets={}
        for item in batch.instruments:
            assets.setdefault(item['asset_id'],dict(id=item['asset_id'],symbol=item.get('spec',{}).get('display_symbol',item.get('spec',{}).get('base_asset',item['asset_id']))[:40],identity_status='native_catalog',known_at=now_ms()))
        asset_rows=[assets[k] for k in sorted(assets)]
        for offset in range(0,len(asset_rows),100):
            s.execute(insert(Asset).values(asset_rows[offset:offset+100]).on_conflict_do_nothing(index_elements=['id']))
        _bulk_merge(s,Instrument,batch.instruments)
        if batch.instruments:
            from engine.pd2.binance_identity import seed
            seed(s,[i['id'] for i in batch.instruments])
        if batch.metadata.get('catalog_complete'):
            current={i['id'] for i in batch.instruments}
            for old in s.scalars(select(Instrument).where(Instrument.venue==batch.venue,Instrument.id.notin_(current))):
                old.spec={**old.spec,'status':'delisted_or_ineligible','catalog_checked_at':now_ms()}
        # PostgreSQL evaluates the conflict predicate and revision against the
        # locked current row, not an earlier SELECT from another transaction.
        candle_updates=[];revision_events=[]
        valid=[]
        for item in candles:
            low,high=Decimal(item["low"]),Decimal(item["high"])
            if low>high or not low<=Decimal(item["open"])<=high or not low<=Decimal(item["close"])<=high: continue
            valid.append({**item,'revision':1})
        valid.sort(key=lambda r:(r['instrument_id'],r['resolution'],r['open_time']))
        for offset in range(0,len(valid),500):
            query=insert(Candle).values(valid[offset:offset+500]);ex=query.excluded
            changed=or_(*(getattr(Candle,k).is_distinct_from(getattr(ex,k)) for k in ('open','high','low','close','base_volume','quote_volume')))
            accepted=and_(ex.received_at>=Candle.received_at,or_(Candle.is_closed.is_(False),ex.is_closed.is_(True)),or_(changed,Candle.is_closed.is_distinct_from(ex.is_closed)))
            updates={c.name:getattr(ex,c.name) for c in Candle.__table__.columns if not c.primary_key and c.name!='revision'}
            updates['revision']=case((and_(Candle.is_closed.is_(True),changed),Candle.revision+1),else_=Candle.revision)
            accepted_rows=s.execute(query.on_conflict_do_update(index_elements=['instrument_id','resolution','open_time'],set_=updates,where=accepted).returning(Candle.__table__)).mappings().all()
            candle_updates.extend(dict(r) for r in accepted_rows)
            revision_events.extend({k:r[k] for k in ('instrument_id','resolution','open_time','revision','raw_ref')} for r in accepted_rows if r['revision']>1)
        points=sorted({(r['instrument_id'],r['event_time']):r for r in batch.oi}.values(),key=lambda r:(r['instrument_id'],r['event_time']))
        for offset in range(0,len(points),500):
            query=insert(OiPoint).values(points[offset:offset+500])
            s.execute(query.on_conflict_do_update(index_elements=['instrument_id','event_time'],set_={c.name:getattr(query.excluded,c.name) for c in OiPoint.__table__.columns if not c.primary_key},where=OiPoint.received_at<=query.excluded.received_at))
        points=sorted({(r['instrument_id'],r['settled_at']):r for r in batch.funding}.values(),key=lambda r:(r['instrument_id'],r['settled_at']))
        for offset in range(0,len(points),500):
            query=insert(FundingPoint).values(points[offset:offset+500])
            # INSERT conflict predicates do not provide SELECT auto-correlation.
            # These fixed outer references must not become inner FROM tables.
            incoming=select(RawEvent.received_at).where(RawEvent.id==literal_column('excluded.raw_ref')).scalar_subquery()
            previous=select(RawEvent.received_at).where(RawEvent.id==literal_column('funding_points.raw_ref')).scalar_subquery()
            s.execute(query.on_conflict_do_update(index_elements=['instrument_id','settled_at'],set_={c.name:getattr(query.excluded,c.name) for c in FundingPoint.__table__.columns if not c.primary_key},where=and_(incoming>=previous,FundingPoint.rate.is_distinct_from(query.excluded.rate))))
        states={}
        for r in batch.states:
            iid=r['instrument_id'];states[iid]=merge_market_payload(states.get(iid,{}),r)
        for iid in sorted(states):
            s.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':'market_state:'+iid})
        old_states={r.instrument_id:r for r in s.scalars(select(MarketState).where(MarketState.instrument_id.in_(states)).order_by(MarketState.instrument_id).with_for_update())}
        for item in states.values():
            old=old_states.get(item['instrument_id'])
            merged=merge_market_payload(old.payload if old else {},item)
            s.merge(MarketState(instrument_id=item["instrument_id"],as_of=merged['as_of'],payload=merged))
        for event in revision_events:emit(s,'candle_revision',event)
        if batch.candle_only:
            if candle_updates:emit(s,"candle_update",{"source":batch.venue,"count":len(candle_updates),'closed_instruments':sorted({r['instrument_id'] for r in candle_updates if r['is_closed']})})
            return
        health=s.get(SourceHealth,batch.venue)
        health.state="partial" if batch.errors else "ok"
        health.checked_at=now_ms()
        health.last_success_at=now_ms() if batch.states else health.last_success_at
        health.message=f"本次更新 {len(batch.states)} 个市场" + (f"；{len(batch.errors)} 项采集缺口" if batch.errors else "")
        health.capabilities={**SOURCE_CAPABILITIES[batch.venue],"latest_gaps":batch.errors,**batch.metadata}
        emit(s,"market_update",{"source":batch.venue,"instruments":[x["instrument_id"] for x in batch.states]})

def merge_market_payload(old,item):
    """WS prices and REST OI have independent clocks; neither discards the other."""
    item=dict(item)
    volume=next((item[k] for k in ('quote_volume_24h','volume_24h_quote','quote_volume') if item.get(k) is not None),None)
    if volume is not None:
        # Keep aliases consistent: an old higher-priority alias must not hide
        # the new 24h observation when the adapter uses another spelling.
        for key in ('quote_volume_24h','volume_24h_quote','quote_volume'):
            item[key]=volume
        item.setdefault('volume_24h_source_at',item.get('as_of'))
        item.setdefault('volume_24h_received_at',item.get('received_at'))
        item.setdefault('volume_24h_raw_ref',item.get('raw_ref'))
    merged={**old,**item}
    if (old.get('volume_24h_source_at') or 0)>(item.get('volume_24h_source_at') or 0):
        for k in ('quote_volume_24h','volume_24h_quote','quote_volume','volume_24h_source_at','volume_24h_received_at','volume_24h_raw_ref'):
            if k in old:merged[k]=old[k]
    if (old.get('as_of') or 0)>(item.get('as_of') or 0):
        for k in ('price','price_kind','as_of','received_at','raw_ref','index_price','predicted_funding_rate','next_funding_time'):
            if k in old:merged[k]=old[k]
    if item.get('oi_status')!='source_time_unknown' and (old.get('oi_as_of') or 0)>(item.get('oi_as_of') or 0):
        for k in ('oi_base','oi_notional_quote','oi_quote_asset','oi_as_of','oi_received_at','oi_status','oi_raw_ref','oi_mark_raw_ref','oi_mark_as_of','oi_notional_status'):
            if k in old:merged[k]=old[k]
    if (old.get('funding_settled_at') or 0)>(item.get('funding_settled_at') or 0):
        for k in ('funding_rate','funding_interval_seconds','funding_settled_at','funding_status','funding_kind','funding_observed_through'):
            if k in old:merged[k]=old[k]
    return merged


def list_markets(instrument_ids=None,light=False):
    now=now_ms();result=[]
    config=runtime_config();max_age=600000
    with Session() as s:
        health=s.get(SourceHealth,"binance")
        universe=health.capabilities if health else {}
        selected_ids=set(universe.get("selected_ids",[]))
        from engine.pd2.universe_policy import membership
        aliases, admitted = membership(s)
        count_query=select(Candle.instrument_id,func.count()).where(Candle.resolution=="240")
        if instrument_ids is not None:count_query=count_query.where(Candle.instrument_id.in_(instrument_ids))
        counts=dict(s.execute(count_query.group_by(Candle.instrument_id)).all())
        # Batch the baselines instead of 3 SQL queries for each market.
        query=select(Instrument,MarketState).join(MarketState,Instrument.id==MarketState.instrument_id)
        if instrument_ids is None:query=query.where(Instrument.venue.in_(REALTIME),Instrument.market_type!='dex_pool')
        if instrument_ids is not None: query=query.where(Instrument.id.in_(instrument_ids))
        states=list(s.execute(query.order_by(Instrument.asset_id,Instrument.venue)))
        ends={inst.id:st.payload.get("oi_as_of") for inst,st in states}
        baselines={}
        for period,duration in [("1H",3600_000),("4H",14400_000),("24H",86400_000)]:
            pairs=[(iid,end-duration) for iid,end in ends.items() if end]
            if not pairs or light: continue
            from sqlalchemy import values, column, String, BigInteger
            targets=values(column("iid",String),column("target",BigInteger),name="targets").data(pairs)
            nearest=(select(OiPoint.instrument_id,OiPoint.event_time,OiPoint.base_quantity,OiPoint.mark_price,OiPoint.notional_quote)
                .join(targets,OiPoint.instrument_id==targets.c.iid).where(OiPoint.event_time<=targets.c.target,OiPoint.event_time>=targets.c.target-max_age)
                .distinct(OiPoint.instrument_id).order_by(OiPoint.instrument_id,OiPoint.event_time.desc()))
            baselines[period]={r.instrument_id:r for r in s.execute(nearest)}
        for inst,state in states:
            if inst.spec.get("crypto_eligible") is False:continue
            p=state.payload;fresh=0<=now-state.as_of<=90000
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
                            extra=paired_changes(old.base_quantity,old.mark_price,Decimal(p["oi_base"]),
                                Decimal(p['oi_notional_quote']) if p.get('oi_notional_quote') is not None else None,inst.quote_asset)
                changes.append(OiChange(period=period,display_label=label,value_pct=value,quality=Quality(status=reason),start_sample_time=start,end_sample_time=end,**extra))
            rate_time=p.get("funding_settled_at");interval=p.get("funding_interval_seconds")
            funding_ok=rate_time is not None and interval is not None and 0<=now-rate_time<=(interval+300)*1000
            count=counts.get(inst.id,0)
            from engine.asset_classification import classification
            result.append(MarketDTO(**classification(inst.id,inst.asset_id,inst.spec),instrument_id=inst.id,asset_id=inst.asset_id,symbol=inst.spec.get("display_symbol",inst.spec.get("base_asset",inst.asset_id)),venue=inst.venue,native_symbol=inst.native_symbol,quote_asset=inst.quote_asset,price_kind=p["price_kind"],price=p.get("price"),as_of=state.as_of,
                quality=Quality(status="fresh" if fresh else "stale",reason=None if fresh else "报价已过期"),
                oi_base=p.get("oi_base"),oi_notional_quote=p.get("oi_notional_quote"),oi_quote_asset=p.get("oi_quote_asset",inst.quote_asset),oi_changes=changes,oi_as_of=p.get("oi_as_of"),
                oi_quality=Quality(status="fresh" if oi_fresh else ("stale" if p.get("oi_as_of") else "not_applicable" if inst.market_type in ("spot","dex_pool") else "missing")),
                funding_rate=p.get("funding_rate"),funding_kind="settled",funding_interval_seconds=interval,funding_settled_at=rate_time,
                funding_quality=Quality(status="fresh" if funding_ok else ("stale" if rate_time else "not_applicable" if inst.market_type in ("spot","dex_pool") else "missing")),candles_count=count,
                volume_24h_quote=p.get("volume_24h_quote"),universe_rank=p.get("universe_rank"),
                in_universe=aliases.get(inst.asset_id,inst.asset_id) in admitted,
                deep_history=p.get("deep_history",inst.venue!="binance")))
    from engine.pd2.providers import valuation_for,valuation_inputs
    if light:return MarketList(as_of=now,rows=result,universe={})
    with Session() as valuation_session:
        inputs=valuation_inputs(result,valuation_session)
        for market in result:
            v=valuation_for(market,valuation_session,now,inputs)
            market.circulating_cap=v["cap"];market.oi_cap_ratio=v["ratio"];market.oi_notional_usd=v["usd"]
            market.valuation_status=v["status"];market.coverage=v
    return MarketList(as_of=now,rows=result,universe={k:v for k,v in universe.items() if k not in ("selected_ids","deep_ids","latest_gaps")})

def candle_targets(venue="binance"):
    """All admitted markets rotate; cold starts use native timeframes, then only tails."""
    from engine.pd2.universe_policy import primary_eligible
    from engine.pd2.collection_schedule import due,acknowledge
    with Session() as s:
        instruments=[i for i in s.scalars(select(Instrument).where(Instrument.venue==venue)) if primary_eligible(i)]
    # due() owns its transaction/lock; do not retain a pooled connection while
    # asking it for another one under concurrent collection and projection.
    chosen=set(due(venue,[i.native_symbol for i in instruments],kind="candles"))
    with Session() as s:
        selected=[i for i in instruments if i.native_symbol in chosen]
        ids=[i.id for i in selected]
        latest={(i,r):(t,received) for i,r,t,received in s.execute(select(Candle.instrument_id,Candle.resolution,func.max(Candle.open_time),func.max(Candle.received_at)).where(Candle.instrument_id.in_(ids)).group_by(Candle.instrument_id,Candle.resolution))}
        from engine.pd2.p35_schedule import windows as required_windows
        window_depth=max([300,*required_windows(runtime_config(s)).values()])+1  # include a forming tail without losing the oldest required close
        ranked=select(Candle.instrument_id,Candle.resolution,Candle.open_time,func.row_number().over(partition_by=(Candle.instrument_id,Candle.resolution),order_by=Candle.open_time.desc()).label('rn')).where(Candle.instrument_id.in_(ids)).subquery()
        opens={}
        for iid,res,at in s.execute(select(ranked.c.instrument_id,ranked.c.resolution,ranked.c.open_time).where(ranked.c.rn<=window_depth).order_by(ranked.c.open_time)):
            opens.setdefault((iid,res),[]).append(at)
        now=now_ms();targets=[]
        closed_through={(iid,res):at for iid,res,at in s.execute(select(Candle.instrument_id,Candle.resolution,func.max(Candle.close_time)).where(
            Candle.instrument_id.in_(ids),Candle.is_closed.is_(True),Candle.close_time<=now).group_by(Candle.instrument_id,Candle.resolution))}
        # A missed close message leaves a real row marked open. It is a data gap,
        # even when open_time values are contiguous. Re-fetch it instead of merely
        # continuing from the newest (possibly still forming) candle forever.
        unfinalized={(iid,res):at for iid,res,at in s.execute(select(Candle.instrument_id,Candle.resolution,func.min(Candle.open_time)).where(
            Candle.instrument_id.in_(ids),Candle.is_closed.is_(False),Candle.close_time<=now-180000).group_by(Candle.instrument_id,Candle.resolution))}
        periods=[("60","1h",3600000),("240","4h",14400000),("1D","1d",86400000),("1W","1w",604800000)]
        cfg=runtime_config(s)
        from engine.pd2.p35_schedule import windows,priority_instruments,hourly_dependencies
        requirements=windows(cfg);active=priority_instruments(s);hourly=hourly_dependencies(s,cfg)
        # Fair discovery retains a small 1H lane even before a 4H structure exists.
        discover_1h={i.id for i in selected[:max(1,len(selected)//4)]}
        for res in ("15","30"):
            if res in cfg.analysis.timeframes:periods.append((res,res+"m",int(res)*60000))
        for inst in selected:
            for resolution,interval,duration in periods:
                if resolution=="60" and inst.id not in hourly and inst.id not in discover_1h:continue
                if resolution not in requirements and resolution not in cfg.analysis.timeframes:continue
                watermark,received=latest.get((inst.id,resolution),(None,0))
                times=opens.get((inst.id,resolution),[])
                gap=next((a+duration for a,b in zip(times,times[1:]) if b-a>duration),None)
                unfinished=unfinalized.get((inst.id,resolution))
                if unfinished is not None:gap=min(gap,unfinished) if gap is not None else unfinished
                if gap is not None:
                    targets.append((inst.id,inst.native_symbol,resolution,interval,min(1500,max(2,(now-gap)//duration+2)),gap));continue
                # All analytical frames are refreshed at their actual close. Live
                # prices/candles remain on the existing stream; do not repeatedly
                # spend REST weight on an already complete closed interval.
                offset=345600000 if resolution=='1W' else 0  # UTC Monday
                expected_close=(now-offset)//duration*duration+offset
                needed=requirements.get(resolution,300)
                # Find a missing prefix even when the latest close is fresh.
                start=expected_close-needed*duration
                onboard=int((inst.spec or {}).get('onboard_time') or 0)
                if onboard:start=max(start,((onboard-offset+duration-1)//duration)*duration+offset)
                present=set(times)
                missing=next((at for at in range(start,expected_close,duration) if at not in present),None)
                if missing is not None:
                    targets.append((inst.id,inst.native_symbol,resolution,interval,min(1000 if venue=='binance_spot' else 1500,max(2,(expected_close-missing)//duration+1)),missing));continue
                if closed_through.get((inst.id,resolution),0)>=expected_close:continue
                limit=requirements.get(resolution,300) if watermark is None else min(1500,max(2,(now-watermark)//duration+2))
                targets.append((inst.id,inst.native_symbol,resolution,interval,limit,watermark))
        pending={t[1] for t in targets}
        complete=[i.native_symbol for i in selected if i.native_symbol not in pending]
    if complete:acknowledge(venue,complete,'candles',now)
    # Restore the trigger frames across the batch before background day/week history.
    # P34's live structure gate is a 4H close. Keep its repair/refresh ahead
    # of 1H and slow context frames under the same shared request budget.
    from engine.pd2.p35_schedule import priority_instruments,target_priority
    with Session() as s:active=priority_instruments(s)
    return sorted(targets,key=lambda t:target_priority(t,active))


def candle_window_ready(iid,resolution):
    from engine.pd2.candle_window import CandleWindow
    from engine.pd2.p35_schedule import windows as requirements
    step={'60':3600000,'240':14400000,'1D':86400000,'1W':604800000,'15':900000,'30':1800000}[resolution]
    now=now_ms();offset=345600000 if resolution=='1W' else 0
    expected=(now-offset)//step*step+offset
    with Session() as session:
        count=requirements(runtime_config(session)).get(resolution,300)
        records=list(session.scalars(select(Candle).where(Candle.instrument_id==iid,Candle.resolution==resolution,Candle.open_time<expected).order_by(Candle.open_time.desc()).limit(count)))
        rows=[{k:getattr(row,k) for k in ('open_time','close_time','is_closed','received_at','open','high','low','close')} for row in records]
    # Hydrate using committed data; WS forming bars cannot manufacture readiness.
    window=CandleWindow(count);window.merge(rows)
    return window.ready(expected,step,count)


def read_events(after,limit=200):
    with Session() as s:
        return [{"id":r.id,"kind":r.kind,"created_at":r.created_at,"payload":r.payload} for r in s.scalars(select(StreamEvent).where(StreamEvent.id>after).order_by(StreamEvent.id).limit(limit))]
