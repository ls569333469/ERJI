from engine.repository import write_heartbeat
from engine.p39.timing import current_execution,QUOTE_MS,BOOK_MS,REFERENCE_MS,REVIEW_MS,WAIT_REVIEW_MS
"""PD2 application service: one immutable decision snapshot for list/chart/TG."""
from decimal import Decimal as D
from hashlib import sha256
import json
from sqlalchemy import select,text,func
from engine.db import Session
from engine.models import *
from engine.adapters.base import now_ms
from engine.repository import runtime_config,list_markets,emit
from engine.contracts import PHASES
from engine.pd2.analysis import technical,choose_targets,weekly_cross_gate,aggregate,VERSION,trailing_protection
from engine.pd2.decision import decide,execution_cost
from engine.pd2.storage import get
from engine.strategies.structures import closed,volume_multiple,candidate

def candle_dict(c):
    return {"open_time":c.open_time,"close_time":c.close_time,"received_at":c.received_at,"is_closed":c.is_closed,
      "open":str(c.open),"high":str(c.high),"low":str(c.low),"close":str(c.close),
      "base_volume":str(c.base_volume) if c.base_volume is not None else None,"quote_volume":str(c.quote_volume) if c.quote_volume is not None else None,
      "revision":c.revision}
def load_bars(s,instrument,now,limit=400):
    result={}
    for resolution in ("60","240","1D","1W"):
        from engine.p38.windows import read
        rows=read(s,instrument,resolution,now,limit,closed_only=False)
        result[resolution]=[{k:str(v) if isinstance(v,D) else v for k,v in c.items()} for c in rows]
    for period in ("120","480","720"):
        result[period]=aggregate(result["60"],period,now)
    # New complete high-timeframe bars can become available from the 1H stream
    # before the next native reconciliation. Never overwrite an already closed native bar.
    for period in ('240','1D','1W'):
        by_time={b['open_time']:b for b in result[period]}
        for bar in aggregate(result['60'],period,now):
            old=by_time.get(bar['open_time'])
            if bar['is_closed'] and (not old or not old['is_closed']):by_time[bar['open_time']]=bar
        result[period]=[by_time[t] for t in sorted(by_time)]
    # Provider weekly boundaries are checked; non-Monday weeks are never used as UTC weeks.
    weeks=result["1W"]
    if weeks and any((b["open_time"]-345600000)%604800000 for b in weeks):
        result["1W"]=aggregate(result["1D"],"1W",now)
    return result
def analyze_market(instrument,now=None,series=True):
    now=now or now_ms();config=runtime_config()
    with Session() as s:
        inst=s.get(Instrument,instrument)
        if not inst:return None
        bars=load_bars(s,instrument,now)
        precision=inst.spec.get("price_precision")
        tick=inst.spec.get("tick_size") or (str(D(10)**(-int(precision))) if precision is not None else "0.00000001")
        assessments=[technical(bars.get(r,[])[-(156 if r=="1W" else 200):],r,now,tick,config.analysis) for r in config.analysis.timeframes]
        from engine.pd2.structural_history import load,merge
        structure=load(s,inst,now,config.analysis,fallback=bars)
        assessments=[merge(a,structure.get(a['timeframe'])) for a in assessments]
        current=s.get(MarketState,instrument)
        price=current.payload.get("price") if current else None
        opportunities=choose_targets(assessments,price) if price else None
        if opportunities:
            from engine.pd2.opportunity import enrich
            opportunities=enrich(opportunities,assessments,bars,price,now,inst.quote_asset)
        if not series:
            for a in assessments:a.pop("series",None)
        return {"instrument_id":instrument,"as_of":now,"data_revision":sha256(json.dumps([bars,{tf:v["coverage"] for tf,v in structure.items()},"pd2-shared-structure-v2"],sort_keys=True).encode()).hexdigest()[:16],
          "algorithm_version":VERSION,"timeframes":assessments,"opportunity":opportunities,
          "weekly_cross_gate":weekly_cross_gate(bars.get("1W",[]),now),"price":price,"quote_asset":inst.quote_asset,
          "validation_status":"unvalidated","probability":None}

def followup_episodes(s,episode,campaign,bars,now):
    e=episode.evidence
    if not e.get("confirmation") or episode.phase not in ("structure_confirmed","trend_continuing"):return []
    result=[];one=closed(bars["60"],now);box=e["box"];u,a=D(box["upper"]),D(box["atr"])
    after=[b for b in one if e["confirmation"]<b["close_time"]<=e["confirmation"]+6*14400000]
    for i,b in enumerate(after):
        if i<1:continue
        touched=[x for x in after[:i] if D(x["low"])<=u+a*D(".25") and D(x["close"])>=u-a*D(".25")]
        vm=volume_multiple(b,[x for x in one if x["close_time"]<b["close_time"]])
        if not touched or D(b["close"])<=u or vm is None or vm<D("1.5"):continue
        key="retest-"+sha256((episode.id+str(b["close_time"])).encode()).hexdigest()[:24]
        # Only the first successful reclaim forms this retest; it does not roll forever.
        if not s.get(Episode,key):
            low=min(D(x["low"]) for x in touched)
            evidence={**e,"template":"retest_entry","parent_episode":episode.id,"detected_at":now,"evaluated_at":now,
              "trigger":{**b,"resolution":"60"},"entry_zone":[str(u),str(u+a*D(".5"))],
              "protection":str(low-a*D(".25")),"expires_at":b["close_time"]+3600000,
              "reason":"4H突破后已实际回踩，1H收回箱顶且成交增强","transitions":[]}
            from engine.p39.pipeline import origin
            evidence['parent_origin']=e.get('origin');evidence['origin']=origin()
            row=Episode(id=key,campaign_id=campaign.id,strategy_id="retest_entry",phase="structure_confirmed",rule_version=VERSION,evidence=evidence)
            s.add(row);result.append((row,campaign))
        break
    recent=candidate(campaign.reference_instrument_id,bars["240"],bars["60"],now)
    if recent and recent.get("trigger") and recent["box"]["start_time"]>e["confirmation"]:
        key="restart-"+recent["id"]
        if not s.get(Episode,key):
            evidence={**recent,"template":"trend_restart","parent_episode":episode.id,"instrument_id":campaign.reference_instrument_id,
              "detected_at":now,"evaluated_at":now,"reason":"活动趋势形成新确认箱体并再次触发","transitions":[]}
            from engine.p39.pipeline import origin
            evidence['parent_origin']=e.get('origin');evidence['origin']=origin()
            row=Episode(id=key,campaign_id=campaign.id,strategy_id="trend_restart",phase=recent["phase"],rule_version=VERSION,evidence=evidence)
            s.add(row);result.append((row,campaign))
    return result

def headline(action,assessment,opportunity,evidence):
    daily=next((a for a in assessment if a["timeframe"]=="1D"),{})
    weekly=next((a for a in assessment if a["timeframe"]=="1W"),{})
    nature="日线趋势启动" if daily.get("direction")=="up" else "下跌中的反弹" if daily.get("direction") in ("down","broken") else "结构突破"
    if daily.get("direction")=="up" and weekly.get("direction")=="up":nature="多周期上涨展开"
    target=opportunity.get("primary") or opportunity.get("t1")
    highest=opportunity.get("highest_conditional_target")
    parts=[action["label"],nature]
    if target:parts.append("主攻 "+target["low"]+"–"+target["high"])
    if highest and highest!=target:parts.append("条件延伸 "+highest["low"]+"–"+highest["high"])
    if evidence.get("protection"):parts.append("保护 "+evidence["protection"])
    return {"headline":"；".join(parts),"nature":nature,"direction":"invalidated" if action["code"]=="ended" else "bullish" if evidence.get("trigger") else "pending",
      "support":[v for v in [evidence.get("reason"),"成交倍数 "+str(next((a.get("volume_multiple") for a in assessment if a["timeframe"]=="240"),None)) if next((a.get("volume_multiple") for a in assessment if a["timeframe"]=="240"),None) is not None else None] if v],"opposition":[a["timeframe"]+"结构"+a["direction"] for a in assessment if a["direction"] in ("down","broken")],
      "missing":[a["timeframe"]+":"+str(a.get("reason")) for a in assessment if a["status"]!="fresh"],
      "invalidation":f'现价触及保护 {evidence.get("protection")}，或4H按原规则收回箱顶 {evidence.get("box",{}).get("upper")} 下方，停止旧计划',
      "validation_status":"unvalidated","probability":None}

def refresh_current(instrument_ids):
    """Refresh entry/protection from a frozen plan without rebuilding structures."""
    from copy import deepcopy
    from engine.pd2.book_clock import effective_time,fresh
    now=now_ms();count=0
    for iid in sorted(set(instrument_ids)):
        with Session.begin() as s:
            s.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':':decisions:'+iid})
            state=s.get(MarketState,iid);inst=s.get(Instrument,iid)
            if not inst:continue
            from engine.p38.plan_queries import latest_ids
            current_plans=list(s.execute(select(Plan,Episode,SignalSnapshot).join(Episode).join(SignalSnapshot,SignalSnapshot.plan_id==Plan.id).where(
                Plan.execution_instrument_id==iid,Episode.phase.notin_(['ended','invalidated']),SignalSnapshot.id.in_(latest_ids([iid])))))
            shared_bars=None
            for plan,episode,latest in current_plans:
                if latest.payload.get('action',{}).get('code')=='ended':continue
                if plan.payload.get('p33'):
                    from engine.p38.p33_current import refresh
                    if shared_bars is None:shared_bars=load_bars(s,iid,now)
                    current=refresh(s,plan,episode,latest,inst,state,now,shared_bars)
                    if current['action']==latest.payload['action'] and current['current_protection']==latest.payload['current_protection'] and now-latest.as_of<5000:continue
                    s.add(SignalSnapshot(id=current['id'],plan_id=plan.id,as_of=now,payload=current))
                    if current['phase']=='invalidated':episode.phase='invalidated'
                    event_id=emit(s,'signal_update',{'asset_id':inst.asset_id,'episode_id':episode.id,'snapshot_id':current['id'],'phase':current['phase']})
                    notifications=runtime_config(s).notifications
                    if notifications.enabled and current['action']['code']!=latest.payload['action']['code']:
                        from engine.pd2.notifications import enqueue
                        enqueue(s,event_id,current,notifications)
                    count+=1;continue
                snap=deepcopy(latest.payload);frozen=plan.payload;cfg=type(runtime_config(s)).model_validate(frozen['config']);cfg.execution=current_execution(cfg.execution)
                original=frozen['evidence'];protection=snap.get('current_protection') or frozen['original_protection']
                from engine.p38.windows import read
                protection_bars=[{k:str(v) if isinstance(v,D) else v for k,v in r.items()} for r in read(s,iid,'240',now,400)]
                if cfg.analysis.trailing_mode=='chandelier':
                    protection=trailing_protection(protection_bars,protection,frozen['original_protection'],now,cfg.analysis.trailing_period,str(cfg.analysis.trailing_atr_multiple))
                phase=snap.get('phase',episode.phase)
                if state and state.payload.get('price') and 0<=now-state.as_of<=QUOTE_MS and D(state.payload['price'])<=D(protection):phase='invalidated'
                if episode.rule_version==VERSION and original.get('trigger') and original.get('box',{}).get('upper'):
                    if any(b['close_time']>original['trigger']['close_time'] and D(b['close'])<D(original['box']['upper']) for b in protection_bars):phase='invalidated'
                cached=s.get(DataCapability,f'{inst.venue}:execution_book:{iid}')
                book=cached.payload if cached and cached.status=='fresh' and cached.expires_at>=now and fresh(cached.payload,now) else None
                quote={'price':book['asks'][0][0],'price_kind':'ask','as_of':effective_time(book)} if book and book.get('asks') else None
                target=frozen.get('original_t1');cost=None
                if plan.expires_at>now:
                    from engine.pd2.providers import quote_usd_rate
                    fx=quote_usd_rate(inst.quote_asset,s,now);ec=cfg.execution
                    if fx:cost=execution_cost(original['entry_zone'][1],protection,target.get('low') if target else None,book,now,str(D(str(ec.probe_notional_usd))/fx),ec.taker_fee_rates.get(inst.venue),ec.funding_scenario_rate,str(ec.early_min_net_r if frozen.get('template')=='early_breakout' else ec.confirmed_min_net_r),str(ec.max_spread_bps),ec.depth_max_age_seconds*1000)
                # A fast current-result update cannot bless an outdated structure.
                assessments=snap.get('timeframe_assessments',[])
                if any(not any(a.get('timeframe')==tf and a.get('status')=='fresh' and 0<=now-a.get('closed_at',0)<=(3600000 if tf=='60' else 14400000)+180000 for a in assessments) for tf in ('60','240')):quote=None
                evidence={**original,'protection':protection}
                action=decide(evidence,phase,quote,now,cost,cfg.analysis.style,cfg.execution.quote_max_age_seconds*1000)
                action['action_existing']='退出旧计划' if action['code']=='ended' else snap.get('opportunity_plan',{}).get('position_management','按原保护管理')
                if action==snap.get('action') and protection==snap.get('current_protection') and phase==snap.get('phase') and (action['code'] not in ('trial','participate') or now-latest.as_of<5000):continue
                snap.update(id=plan.id+':'+str(now),as_of=now,known_at=now,computed_at=now,action=action,phase=phase,phase_label=PHASES[phase],evidence=evidence,current_protection=protection,execution=cost,analysis_computed_at=snap.get('analysis_computed_at',latest.as_of),valid_until=min(plan.expires_at,(effective_time(book) or 0)+QUOTE_MS,(book or {}).get('received_at',0)+BOOK_MS) if quote else now)
                if state:
                    snap['market']={**snap.get('market',{}),'price':state.payload.get('price'),'price_kind':state.payload.get('price_kind'),'as_of':state.as_of,'quality':{'status':'fresh' if 0<=now-state.as_of<=QUOTE_MS else 'stale'}}
                from engine.pd2.tracking import advance
                snap['tracking']=advance(latest.payload,snap.get('opportunity_plan',{}),phase,protection,now,bool(state and 0<=now-state.as_of<=QUOTE_MS))
                # A changed current action must not retain the previous buy thesis.
                snap['trade_thesis']={**snap.get('trade_thesis',{}),'headline':action['reason']}
                s.add(SignalSnapshot(id=snap['id'],plan_id=plan.id,as_of=now,payload=snap))
                if phase=='invalidated':episode.phase='invalidated'
                event_id=emit(s,'signal_update',{'asset_id':inst.asset_id,'episode_id':episode.id,'snapshot_id':snap['id'],'phase':phase})
                notifications=runtime_config(s).notifications
                if notifications.enabled and (action['code']!=latest.payload.get('action',{}).get('code') or phase!=latest.payload.get('phase')):
                    from engine.pd2.notifications import enqueue
                    enqueue(s,event_id,snap,notifications)
                count+=1
    return {'snapshots':count}

def refresh_plans(instrument_ids=None,new_only=False):
    # Each native market commits independently. A slow market holds no sibling lock.
    if instrument_ids is None or len(instrument_ids)>1:
        with Session() as read:
            ids=list(instrument_ids) if instrument_ids is not None else list(read.scalars(select(Campaign.reference_instrument_id).distinct()))
        return {'snapshots':sum(refresh_plans([iid],new_only=new_only)['snapshots'] for iid in sorted(set(ids)))}
    now=now_ms();config=runtime_config();markets={m.instrument_id:m for m in list_markets(instrument_ids).rows}
    from engine.pd2.portfolio import portfolio,plan_risk
    portfolio_risk=portfolio()["risk"]
    # Preload native inputs, then release the read connection before reusable
    # technical calculation. The writer lock is reserved for plan handoff.
    prepared={}
    with Session() as read:
        native_bars={iid:load_bars(read,iid,now) for iid in instrument_ids}
        native_specs={i.id:i.spec for i in read.scalars(select(Instrument).where(Instrument.id.in_(instrument_ids)))}
        plan_configs=[p.payload['config'] for p in read.scalars(select(Plan).where(
            Plan.execution_instrument_id.in_(instrument_ids))) if not p.payload.get('p33') and p.payload.get('config')]
    for iid,source in native_bars.items():
        spec=native_specs.get(iid,{})
        tick=spec.get('tick_size') or str(D(10)**(-int(spec.get('price_precision',8))))
        settings=[config.analysis]+[type(config).model_validate(value).analysis for value in plan_configs]
        for cfg in settings:
            signature=sha256(json.dumps(cfg.model_dump(mode='json'),sort_keys=True).encode()).hexdigest()
            key=(iid,signature)
            if key not in prepared:prepared[key]=[technical(source.get(r,[])[-(156 if r=='1W' else 200):],r,now,tick,cfg) for r in cfg.timeframes]
    with Session.begin() as s:
        s.execute(text("SELECT pg_advisory_xact_lock(hashtext(current_schema() || :scope))"),{'scope':':decisions:'+','.join(sorted(instrument_ids or ['all']))})
        # Compare loaded rows (including forming bars), not just a time bucket.
        if any(load_bars(s,iid,now)!=source for iid,source in native_bars.items()):return {'snapshots':0,'superseded':1}
        from engine.models import MarketState
        # A newer quote queues a fast recheck; it does not invalidate closed
        # candle structure inputs. Qualification still checks the captured
        # quote/book age, and the API checks today's price before exposing buy.
        query=select(Episode,Campaign).join(Campaign).where(Episode.phase.notin_(('ended','invalidated')))
        if instrument_ids is not None:query=query.where(Campaign.reference_instrument_id.in_(instrument_ids))
        pairs=list(s.execute(query))
        # Followups must not recursively generate followups from themselves.
        for episode,campaign in list(pairs):
            if episode.rule_version.startswith('p33-'):continue
            if episode.rule_version==VERSION:continue
            bars=load_bars(s,campaign.reference_instrument_id,now)
            pairs.extend(followup_episodes(s,episode,campaign,bars,now))
        s.flush()
        count=0;shared_derivatives=None
        for episode,campaign in pairs:
            if episode.rule_version.startswith('p33-'):continue
            m=markets.get(campaign.reference_instrument_id)
            if not m:continue
            e=dict(episode.evidence);e["instrument_id"]=m.instrument_id
            if not e.get("trigger"):continue
            plan_id=episode.id+":"+("confirmed" if e.get("confirmation") else "early")
            plan=s.get(Plan,plan_id)
            if not plan and episode.phase in ("invalidated","ended"):
                continue
            latest=(s.scalar(select(SignalSnapshot).where(SignalSnapshot.plan_id==plan.id)
                .order_by(SignalSnapshot.as_of.desc()).limit(1)) if plan else None)
            previous=latest.payload if latest else {}
            if previous.get("action",{}).get("code")=="ended":
                continue
            selection=s.get(UserPreference,"main_market:"+m.asset_id)
            selected=(selection.payload if selection else {}).get("selected")
            # Each market is scanned on its own prices. Never transplant a box or plan.
            if not plan and selected and selected!=m.instrument_id:
                matching=any(c.asset_id==m.asset_id and c.reference_instrument_id==selected and ep.evidence.get("trigger") and ep.phase not in ("ended","invalidated") for ep,c in pairs)
                if matching:continue
            if plan and plan.execution_instrument_id!=m.instrument_id:continue
            if new_only and plan:continue
            if not plan and (e.get('expires_at') or 0)<=now:continue
            bars=native_bars[m.instrument_id]
            analysis_config=type(config).model_validate(plan.payload["config"]).analysis if plan else config.analysis
            inst=s.get(Instrument,m.instrument_id)
            tick=inst.spec.get("tick_size") or str(D(10)**(-int(inst.spec.get("price_precision",8))))
            signature=sha256(json.dumps(analysis_config.model_dump(mode='json'),sort_keys=True).encode()).hexdigest()
            from copy import deepcopy
            if (m.instrument_id,signature) not in prepared:return {'snapshots':count,'superseded':1}
            assessments=deepcopy(prepared[(m.instrument_id,signature)])
            if not plan:
                from engine.pd2.structural_history import load,merge
                structure=load(s,inst,now,analysis_config,fallback=bars)
                assessments=[merge(a,structure.get(a['timeframe'])) for a in assessments]
            entry=e.get("entry_zone",[None,None])[1]
            if not entry or not e.get("protection"):continue
            entry_basis=(D(e["entry_zone"][0])+D(e["entry_zone"][1]))/2
            opportunity=choose_targets(assessments,entry,e.get("box"),entry_basis,bool(e.get("confirmation")))
            template=e.get("template") or ("confirmed_breakout" if e.get("confirmation") else "early_breakout")
            if not plan:
                payload={"template":template,"evidence":e,"opportunity":opportunity,"config":config.model_dump(),
                  "config_version":s.scalar(select(SettingsVersion.id).where(SettingsVersion.state=="published").order_by(SettingsVersion.id.desc()).limit(1)),
                  "created_at":now,"original_protection":e["protection"],"original_t1":opportunity["t1"],
                  "first_signal_at":campaign.first_seen_at,"first_signal_price":str(campaign.first_seen_price),
                  "rule_version":VERSION,"validation_status":"unvalidated",
                  "origin":e.get('origin') or {'origin_type':'legacy_unlinked'}}
                plan=Plan(id=plan_id,episode_id=episode.id,execution_instrument_id=m.instrument_id,revision=1,expires_at=e["expires_at"],payload=payload)
                s.add(plan);s.flush()
            frozen=plan.payload
            # Freeze each plan's risk/targets/config; new snapshots track management separately.
            original=frozen["evidence"];target=frozen["original_t1"]
            config_at_plan=type(config).model_validate(frozen["config"]);config_at_plan.execution=current_execution(config_at_plan.execution)
            book_entry=s.get(DataCapability,f"{m.venue}:execution_book:{m.instrument_id}")
            book=book_entry.payload if book_entry and book_entry.status=="fresh" and book_entry.expires_at>=now else None
            from engine.pd2.book_clock import effective_time
            quote={"price":book["asks"][0][0],"price_kind":"ask","as_of":effective_time(book)} if book and book.get("asks") else None
            cost=None
            # Frozen targets/resistance remain reference information. Entry is
            # decided from the strategy, its zone, validity and native book.
            ec=config_at_plan.execution
            from engine.pd2.providers import quote_usd_rate
            fx=quote_usd_rate(m.quote_asset,s,now)
            cost=execution_cost(original["entry_zone"][1],original["protection"],target.get('low') if target else None,book,now,
               str(D(str(ec.probe_notional_usd))/fx) if fx else "0",ec.taker_fee_rates.get(m.venue),ec.funding_scenario_rate,
               str(ec.early_min_net_r if template=="early_breakout" else ec.confirmed_min_net_r),str(ec.max_spread_bps),ec.depth_max_age_seconds*1000) if fx else {"status":"fx_missing","net_r":None,"reasons":["测试金额USD缺少同期报价币汇率"]}
            cost.update(probe_notional_usd=str(ec.probe_notional_usd),quote_asset=m.quote_asset,quote_usd_rate=str(fx) if fx else None)
            protection=previous.get("current_protection") or original["protection"]
            if config_at_plan.analysis.trailing_mode=="chandelier":
                protection=trailing_protection(bars["240"],protection,original["protection"],now,
                    config_at_plan.analysis.trailing_period,str(config_at_plan.analysis.trailing_atr_multiple))
            phase=episode.phase
            if previous.get("action",{}).get("code")=="ended":phase="invalidated"
            if m.price is not None and m.quality.status=="fresh" and 0<=now-m.as_of<=QUOTE_MS and m.price<=D(protection):phase="invalidated"
            if phase not in ("invalidated","ended") and episode.rule_version==VERSION:
                recent_closed=[b for b in closed(bars["240"],now) if b["close_time"]>original["trigger"]["close_time"]]
                if any(D(b["close"])<D(original["box"]["upper"]) for b in recent_closed):phase="invalidated"
            current_e={**original,"protection":protection,"reason":e.get("reason",original.get("reason"))}
            from engine.pd2.event_overlays import attach_event_overlays
            event_overlays=attach_event_overlays(assessments,bars,original,now,previous.get("event_overlays"))
            from engine.pd2.persistent_avwap import update as advance_avwap,read_series as avwap_series
            trigger=original['trigger'];tf=trigger['resolution']
            if trigger.get('open_time') is not None and tf in bars:
                avwap_state=advance_avwap(s,episode.id,m.instrument_id,tf,trigger['open_time'],now)
                event_overlays['avwap']=avwap_state
                values={p['time']:p['event_avwap'] for p in avwap_series(s,avwap_state,
                    min((b['open_time'] for b in bars.get(tf,[])),default=now),now)}
                for assessment in assessments:
                    if assessment['timeframe']==tf:
                        for point in assessment.get('series',[]):point['event_avwap']=values.get(point['time'])
            required_tfs={"60","240"}
            stale_required=[tf for tf in required_tfs if not any(a["timeframe"]==tf and a["status"]=="fresh" for a in assessments)]
            if stale_required:quote=None
            action=decide(current_e,phase,quote,now,cost,config_at_plan.analysis.style,config_at_plan.execution.quote_max_age_seconds*1000)
            sizing=plan_risk(portfolio_risk,original["entry_zone"][1],protection,cost)
            if phase=="invalidated":action["action_existing"]="保护已触发，退出旧计划"
            frozen_t1=dict(frozen["original_t1"]) if frozen["original_t1"] else None
            if frozen_t1 and entry_basis>0:
                frozen_t1.update(remaining_upside_pct=str((D(frozen_t1["low"])/entry_basis-1)*100),price_multiple=str(D(frozen_t1["low"])/entry_basis),
                    basis_price=str(entry_basis),basis_kind="entry_zone_midpoint")
            opportunity={**frozen["opportunity"],"t1":frozen_t1}
            from engine.pd2.opportunity import enrich
            opportunity=enrich(opportunity,assessments,bars,m.price,now,m.quote_asset,action,basis_price=entry_basis)
            from engine.pd2.tracking import advance
            tracking=advance(previous,opportunity,phase,protection,now,
                m.price is not None and m.quality.status=="fresh" and 0<=now-m.as_of<=QUOTE_MS)
            action["action_existing"]="退出旧计划" if action["code"]=="ended" else opportunity["position_management"]
            thesis=headline(action,assessments,opportunity,current_e)
            from engine.pd2.derivatives import context_for,shared_inputs
            if shared_derivatives is None:
                shared_derivatives=shared_inputs(now)
            derivative_context=context_for(m,now,shared_derivatives)
            frozen_derivative_history,daily_path=decision_derivative_evidence(m.instrument_id,derivative_context,bars,now)
            derivative_context["daily_oi_path"]=daily_path
            if derivative_context.get("cross_market_oi"):
                from engine.pd2.oi_aggregate import record_snapshot
                record_snapshot(s,m.asset_id,derivative_context["cross_market_oi"],now)
            snap={"plan_id":plan.id,"episode_id":episode.id,"event_id":episode.id,"strategy_id":frozen["template"],
              "origin":frozen.get('origin') or {'origin_type':'legacy_unlinked'},'pipeline_tier':'T3','analysis_market_id':m.instrument_id,
              "strategy_version":original.get("box",{}).get("rule_version",frozen["rule_version"]),"series_revision":sha256(json.dumps(bars,sort_keys=True).encode()).hexdigest()[:16],
              "instrument_id":m.instrument_id,"asset_id":m.asset_id,
              "rule_version":VERSION,"config_version":frozen["config_version"],"as_of":now,"known_at":now,
              "phase":phase,"phase_label":PHASES[phase],"position_sizing":sizing,"action":action,"trade_thesis":thesis,"evidence":current_e,
              "valid_until":min(now+90000,(quote.get("as_of",now) if quote else now)+config_at_plan.execution.quote_max_age_seconds*1000,(book.get("received_at",0)+config_at_plan.execution.depth_max_age_seconds*1000) if book else now),"execution":cost,"timeframe_assessments":assessments,"opportunity_plan":opportunity,
              "original_opportunity":frozen["opportunity"],"candles_at_snapshot":bars,"derivative_history_at_snapshot":frozen_derivative_history,"original_protection":original["protection"],
              "current_protection":protection,"first_seen_at":campaign.first_seen_at,"first_seen_price":str(campaign.first_seen_price),
              "tracking":tracking,"event_overlays":event_overlays,
              "market":m.model_dump(mode="json"),"market_comparison":(selection.payload if (selection:=s.get(UserPreference,"main_market:"+m.asset_id)) else {"selected":m.instrument_id,"status":"comparison_pending"}),"validation_status":"unvalidated","probability":None,
              "weekly_cross_gate":weekly_cross_gate(bars.get("1W",[]),now),"derivatives":derivative_context}
            snap["id"]=plan.id+":"+str(now)
            from engine.pd2.chart_contract import drawing_contract
            snap["drawing_contract"]=drawing_contract(snap)
            if previous and now-latest.as_of<3000 and action==previous.get("action") and phase==previous.get("phase"):
                continue
            from engine.pd2.snapshots import freeze_datasets
            stored=freeze_datasets(s,snap)
            s.add(SignalSnapshot(id=snap["id"],plan_id=plan.id,as_of=now,payload=stored))
            # Action transitions only; price ticks do not enqueue duplicate alerts.
            if action["code"]!=previous.get("action",{}).get("code") or phase!=previous.get("phase"):
                event_id=emit(s,"signal_update",{"asset_id":m.asset_id,"episode_id":episode.id,"snapshot_id":snap["id"],"phase":phase})
                if config.notifications.enabled:
                    from engine.pd2.notifications import enqueue
                    enqueue(s,event_id,snap,config.notifications)
            count+=1
        write_heartbeat(s,"decisions",now,"ok",{"snapshots":count,"rule_version":VERSION})
    return {"snapshots":count}

def decision_list(view="stages",instrument_ids=None,fast=False):
    # Background projection builder only; HTTP reads the persisted compact table.
    with Session() as session:
        ids=list(instrument_ids) if instrument_ids is not None else list(session.scalars(select(Campaign.reference_instrument_id).distinct()))
    result=[];derivative_cache={};shared_derivatives=None;current_config=runtime_config();candidate_min_net_r=current_config.execution.confirmed_min_net_r
    current_markets={m.instrument_id:m for m in list_markets(ids).rows if m.venue in ('binance','binance_spot','binance_alpha','wallet_rising')}
    # Validate the quote snapshot after reading it. A concurrent WS tick must
    # not be compared with a timestamp captured before the query began.
    now=now_ms()
    def derivative_context_for(market):
        nonlocal shared_derivatives
        if market.asset_id not in derivative_cache:
            from engine.pd2.derivatives import context_for,shared_inputs
            if shared_derivatives is None:
                shared_derivatives=shared_inputs(now)
            derivative_cache[market.asset_id]=context_for(market,now,shared_derivatives,True)
        return derivative_cache[market.asset_id]
    # Detach immutable snapshots and candidate facts before rendering JSON.
    # Historical candidates retain their frozen branch and do not refresh OI.
    with Session() as s:
        from engine.p38.plan_queries import latest_ids
        latest=list(s.scalars(select(SignalSnapshot).where(SignalSnapshot.id.in_(latest_ids(instrument_ids)))))
        episode_query=select(Episode,Campaign).join(Campaign).order_by(Campaign.first_seen_at.desc())
        if instrument_ids is not None:episode_query=episode_query.where(Campaign.reference_instrument_id.in_(instrument_ids))
        episode_rows=[] if fast else list(s.execute(episode_query))
        instruments={i.id:i for i in s.scalars(select(Instrument).where(Instrument.id.in_(current_markets)))} if episode_rows else {}
        heartbeat=s.get(WorkerHeartbeat,"signals")
        from engine.p40.diagnostics import scan_view
        scan={**scan_view(heartbeat.details if heartbeat else {}),"as_of":heartbeat.updated_at if heartbeat else None}
    if not fast:
        active_candidates={campaign.reference_instrument_id for episode,campaign in episode_rows
            if not episode.evidence.get('trigger') and episode.phase not in ('ended','invalidated')}
        for iid in sorted(active_candidates):
            if iid in current_markets:derivative_context_for(current_markets[iid])
    oi_updates={}
    # Preserve all plan records, select the current viable plan for each asset.
    priority={"participate":0,"trial":1,"waiting":2,"paused":3,"no_chase":4,"expired":5,"ended":6}
    latest.sort(key=lambda r:(priority.get(r.payload["action"]["code"] if 0<=now-r.as_of<=90000 and (r.payload["action"]["code"] not in ("participate","trial") or now<=r.payload.get("valid_until",r.as_of+QUOTE_MS)) else "paused",9),-r.as_of))
    for snapshot in latest:
        p=snapshot.payload;asset=p["asset_id"]
        if p["instrument_id"] not in current_markets:continue
        fresh=0<=now-p["as_of"]<=90000 and (p["action"]["code"] not in ("participate","trial") or now<=p.get("valid_until",p["as_of"]+QUOTE_MS))
        action=p["action"] if fresh or p["action"]["code"]=="ended" else {"code":"paused","label":"数据暂停","reason":"报价或决策已过期，等待后台重新确认"}
        if p.get("strategy_id") in current_config.disabled_strategies:
            action={"code":"paused","label":"策略已关闭","reason":"用户关闭此策略；保留历史事件，停止新参与建议"}
        result.append({"id":p["episode_id"],"snapshot_id":snapshot.id,"plan_id":p["plan_id"],
          "asset_id":asset,"instrument_id":p["instrument_id"],"phase":p["phase"],"phase_label":p["phase_label"],
          "action":action,"first_seen_at":p["first_seen_at"],"first_seen_price":p["first_seen_price"],
          "since_first_pct":str((D(p["market"]["price"])/D(p["first_seen_price"])-1)*100) if fresh and p["market"].get("price") else None,
          "fresh":fresh,"decision_as_of":p['as_of'],"valid_until":p.get("valid_until",p["as_of"]+QUOTE_MS),"market":p["market"],"evidence":p["evidence"],"trade_thesis":p["trade_thesis"] if fresh else {**p["trade_thesis"],"headline":"暂停参与；报价或决策已过期，原计划保护继续"},
          "opportunity_plan":p["opportunity_plan"],"derivatives":p.get("derivatives"),"execution":p["execution"],"timeframe_assessments":p["timeframe_assessments"],
          "candidate_min_net_r":candidate_min_net_r})
    # Pending structures remain visible before a plan can exist.
    for episode,campaign in episode_rows:
        # Project every untriggered episode. Asset-level selection belongs to
        # the read projection, after freshness and opportunity qualification.
        if episode.evidence.get("trigger"):continue
        if campaign.reference_instrument_id not in current_markets:continue
        display_market=current_markets[campaign.reference_instrument_id]
        inst=instruments[campaign.reference_instrument_id]
        historical=episode.phase in ('ended','invalidated')
        derivative_context=({"cross_market_oi":{"status":"historical_not_refreshed","markets":[],"total_oi_usd":None},
            "derivatives_reference_market":{"instrument_id":None,"selection":"historical_not_refreshed","observed_at":None},
            "funding_reference":{},"facts":[],"as_of":None} if historical else derivative_context_for(display_market))
        if not historical and derivative_context.get("cross_market_oi"):
            oi_updates[campaign.asset_id]=derivative_context["cross_market_oi"]
        result.append({"id":episode.id,"asset_id":campaign.asset_id,"instrument_id":inst.id,
          "phase":episode.phase,"phase_label":PHASES[episode.phase],
          "action":{"code":"waiting" if episode.phase=="structure_pending" else "ended","label":"等待突破" if episode.phase=="structure_pending" else "停止旧结构","reason":episode.evidence.get("reason")},
          "first_seen_at":campaign.first_seen_at,"first_seen_price":str(campaign.first_seen_price),"since_first_pct":None,
          "fresh":0<=now-display_market.as_of<=180000 and (not episode.evidence.get("price_pattern") or (episode.evidence.get("data_status")!="missing_data" and episode.evidence.get("structure_valid_until",0)>=now)),"market":display_market.model_dump(mode="json"),"evidence":episode.evidence,
          "derivatives":derivative_context,"candidate_min_net_r":candidate_min_net_r})
    # Keep the existing aggregate facts; write in deterministic short batches.
    updates=sorted(oi_updates.items())
    for offset in range(0,len(updates),32):
        with Session.begin() as s:
            from engine.pd2.oi_aggregate import record_snapshot
            for asset,context in updates[offset:offset+32]:record_snapshot(s,asset,context,now)
    from engine.pd2.signal_projection import publish
    if instrument_ids is None:publish(result)
    else:publish(result,scope=instrument_ids,built_at=now,replace_scope=not fast)
    all_rows=result
    if view in ("recommended","trial"):
        desired="trial" if view=="trial" else "participate"
        result=[r for r in result if r["action"]["code"]==desired]
    result.sort(key=lambda r:(priority.get(r["action"]["code"],9),-r["first_seen_at"],r["asset_id"]))
    candidates=[{**r,"blockers":[{"code":r["action"]["code"],"label":r["action"]["reason"]}]} for r in all_rows if r["action"]["code"] not in ("participate","trial")]
    return {"as_of":now,"strategy_status":"pd2_decisions","execution_status":"native_book_checked_cost_reference","view":view,"rows":result,
      "unique_asset_count":len(result),"scan":scan,
      "groups":[{"phase_id":k,"label":v,"asset_count":sum(r["phase"]==k for r in result)} for k,v in PHASES.items()],
      "eligibility":{"status":"enabled","reason":"每个计划按策略触发、买入区间、有效期及真实报价和原生盘口评估；目标与成本估算仅作参考。",
        "missing_checks":[],"total_events":len(all_rows),"confirmed_events":sum(r["phase"] in ("structure_confirmed","trend_continuing") for r in all_rows),
        "early_events":sum(r["phase"]=="early_trigger" for r in all_rows),"waiting_events":sum(r["phase"]=="structure_pending" for r in all_rows),
        "candidate_count":len(candidates),"candidates":candidates}}

def decision_derivative_evidence(instrument,context,bars,now):
    """Freeze the verified contract's own price/OI window, including UTC endpoints."""
    from engine.strategies.oi_paths import classify
    reference=(context.get('derivatives_reference_market') or {}).get('instrument_id') or instrument
    days=bars.get('1D',[])
    if reference!=instrument:
        with Session() as session:
            days=[candle_dict(c) for c in session.scalars(select(Candle).where(
                Candle.instrument_id==reference,Candle.resolution=='1D',Candle.is_closed.is_(True),
                Candle.close_time<=now,Candle.received_at<=now).order_by(Candle.open_time.desc()).limit(3))]
    closed=sorted((b for b in days if b.get('is_closed') and max(b['close_time'],b.get('received_at',0))<=now),key=lambda b:b['open_time'])[-2:]
    endpoints={b[k] for b in closed for k in ('open_time','close_time')}
    history=derivative_history(reference,now,limit=1000,endpoints=endpoints)
    path=classify(closed,(history or {}).get('oi',[]),now)
    path.update(price_market_id=reference,oi_market_id=reference)
    return history,path


def derivative_history(instrument,before=None,limit=500,endpoints=()):
    now=before or now_ms();before=before or now
    with Session() as s:
        inst=s.get(Instrument,instrument)
        if not inst:return None
        oi=list(reversed(list(s.scalars(select(OiPoint).where(OiPoint.instrument_id==instrument,OiPoint.event_time<=before,OiPoint.received_at<=before).order_by(OiPoint.event_time.desc()).limit(limit)))))
        # Dense recent observations must not evict the exact closed-day endpoints.
        retained={p.event_time:p for p in oi}
        for at in sorted(endpoints):
            p=s.scalar(select(OiPoint).where(OiPoint.instrument_id==instrument,OiPoint.event_time<=min(at,before),
                OiPoint.event_time>=at-300000,OiPoint.received_at<=before).order_by(OiPoint.event_time.desc()).limit(1))
            if p:retained[p.event_time]=p
        oi=sorted(retained.values(),key=lambda p:p.event_time)
        funding=list(reversed(list(s.scalars(select(FundingPoint).join(RawEvent,FundingPoint.raw_ref==RawEvent.id).where(FundingPoint.instrument_id==instrument,FundingPoint.settled_at<=before,RawEvent.received_at<=before).order_by(FundingPoint.settled_at.desc()).limit(limit)))))
        return {"instrument_id":instrument,"quote_asset":inst.quote_asset,"as_of":before,"quantity_unit":inst.spec.get("base_asset",inst.asset_id),
          "oi":[{"time":p.event_time,"quantity":str(p.base_quantity) if p.base_quantity is not None else None,
              "notional_quote":str(p.notional_quote) if p.notional_quote is not None else None,"received_at":p.received_at} for p in oi],
          "funding":[{"time":p.settled_at,"rate":str(p.rate),"interval_seconds":p.interval_seconds,"kind":"settled"} for p in funding],
          "oi_status":"fresh" if oi and now-oi[-1].event_time<=180000 else "stale" if oi else "insufficient_history",
          "funding_status":"available" if funding else "insufficient_history","scope":"single_native_instrument"}


def referenced_derivative_history(instrument,before=None,limit=500):
    from engine.pd2.universe_policy import membership
    from engine.pd2.derivatives import choose_reference
    with Session() as session:
        source=session.get(Instrument,instrument)
        if not source:return None
        aliases,_=membership(session)
        canonical=aliases.get(source.asset_id,source.asset_id)
        members=[asset for asset,parent in aliases.items() if parent==canonical]
        members=list(set(members+[source.asset_id,canonical]))
        catalog=[{'instrument_id':i.id,'venue':i.venue,'fresh_oi':False} for i in session.scalars(select(Instrument).where(
            Instrument.asset_id.in_(members),Instrument.market_type.in_(['linear_perpetual','perpetual'])))
            if i.spec.get('status') not in ('CLOSED','DELISTED','inactive','delisted','delisted_or_ineligible')]
        selected=choose_reference(catalog,instrument)
    result=derivative_history(selected['instrument_id'] if selected else instrument,before,limit)
    if result:result['requested_price_market_id']=instrument
    return result

def chart_snapshot(instrument,episode=None,snapshot_id=None):
    with Session() as s:
        if snapshot_id and snapshot_id.startswith('p33-research-'):
            raw=s.get(RawEvent,snapshot_id)
            if not raw or raw.endpoint!='p33_snapshot' or raw.payload.get('instrument_id')!=instrument:return None
            from engine.pd2.snapshots import hydrate
            return hydrate(s,raw.payload)
        snapshot=s.get(SignalSnapshot,snapshot_id) if snapshot_id else s.scalar(select(SignalSnapshot).join(Plan).where(Plan.episode_id==episode,Plan.execution_instrument_id==instrument).order_by(SignalSnapshot.as_of.desc()).limit(1)) if episode else None
        if snapshot and snapshot.payload["instrument_id"]!=instrument:return None
        if snapshot_id and not snapshot:return None
        from engine.pd2.snapshots import hydrate
        payload=hydrate(s,snapshot.payload) if snapshot else None
        if payload and not payload.get("series_revision"):
            # Legacy metadata only: identity is derived from the already frozen dataset,
            # never from today's candle table. No stored historical values are rewritten.
            payload["series_revision"]=(payload.get("datasets") or {}).get("candles_at_snapshot") or sha256(
                json.dumps(payload.get("candles_at_snapshot") or {},sort_keys=True).encode()).hexdigest()[:16]
        if payload and "drawing_contract" not in payload:
            from engine.pd2.chart_contract import drawing_contract
            payload["drawing_contract"]=drawing_contract(payload)
        elif payload and payload.get("drawing_contract") and not payload["drawing_contract"].get("series_revision"):
            payload["drawing_contract"]={**payload["drawing_contract"],"series_revision":payload["series_revision"]}
        if payload and payload["action"]["code"]!="ended" and not snapshot_id and (now_ms()-payload["as_of"]>90000 or (payload["action"]["code"] in ("participate","trial") and now_ms()>payload.get("valid_until",payload["as_of"]+QUOTE_MS))):
            payload["action"]={"code":"paused","label":"暂停参与","reason":"报价或决策已过期，等待重新评估"}
            payload["trade_thesis"]={**payload["trade_thesis"],"headline":"暂停参与；报价或决策已过期，原计划保护继续"}
            payload["drawing_contract"]={**payload["drawing_contract"],"entry_zone":None,"protection":None,"first_target":None}
        return payload
