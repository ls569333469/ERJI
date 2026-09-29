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
        rows=list(s.scalars(select(Candle).where(Candle.instrument_id==instrument,Candle.resolution==resolution,
          Candle.received_at<=now).order_by(Candle.open_time.desc()).limit(limit)))
        result[resolution]=[candle_dict(c) for c in reversed(rows)]
    for period in ("120","480","720"):
        result[period]=aggregate(result["60"],period,now)
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
        current=s.get(MarketState,instrument)
        price=current.payload.get("price") if current else None
        opportunities=choose_targets(assessments,price) if price else None
        if opportunities:
            from engine.pd2.opportunity import enrich
            opportunities=enrich(opportunities,assessments,bars,price,now,inst.quote_asset)
        if not series:
            for a in assessments:a.pop("series",None)
        return {"instrument_id":instrument,"as_of":now,"data_revision":sha256(json.dumps(bars,sort_keys=True).encode()).hexdigest()[:16],
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
            row=Episode(id=key,campaign_id=campaign.id,strategy_id="retest_entry",phase="structure_confirmed",rule_version=VERSION,evidence=evidence)
            s.add(row);result.append((row,campaign))
        break
    recent=candidate(campaign.reference_instrument_id,bars["240"],bars["60"],now)
    if recent and recent.get("trigger") and recent["box"]["start_time"]>e["confirmation"]:
        key="restart-"+recent["id"]
        if not s.get(Episode,key):
            evidence={**recent,"template":"trend_restart","parent_episode":episode.id,"instrument_id":campaign.reference_instrument_id,
              "detected_at":now,"evaluated_at":now,"reason":"活动趋势形成新确认箱体并再次触发","transitions":[]}
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

def refresh_plans(instrument_ids=None):
    now=now_ms();config=runtime_config();markets={m.instrument_id:m for m in list_markets().rows}
    from engine.pd2.portfolio import portfolio,plan_risk
    portfolio_risk=portfolio()["risk"]
    with Session.begin() as s:
        s.execute(text("SELECT pg_advisory_xact_lock(1877203)"))
        query=select(Episode,Campaign).join(Campaign)
        if instrument_ids is not None:query=query.where(Campaign.reference_instrument_id.in_(instrument_ids))
        pairs=list(s.execute(query))
        # Followups must not recursively generate followups from themselves.
        for episode,campaign in list(pairs):
            if episode.rule_version==VERSION:continue
            bars=load_bars(s,campaign.reference_instrument_id,now)
            pairs.extend(followup_episodes(s,episode,campaign,bars,now))
        s.flush()
        count=0
        for episode,campaign in pairs:
            m=markets.get(campaign.reference_instrument_id)
            if not m:continue
            e=dict(episode.evidence);e["instrument_id"]=m.instrument_id
            if not e.get("trigger"):continue
            plan_id=episode.id+":"+("confirmed" if e.get("confirmation") else "early")
            plan=s.get(Plan,plan_id)
            selection=s.get(UserPreference,"main_market:"+m.asset_id)
            selected=(selection.payload if selection else {}).get("selected")
            # Each market is scanned on its own prices. Never transplant a box or plan.
            if not plan and selected and selected!=m.instrument_id:
                matching=any(c.asset_id==m.asset_id and c.reference_instrument_id==selected and ep.evidence.get("trigger") and ep.phase not in ("ended","invalidated") for ep,c in pairs)
                if matching:continue
            if plan and plan.execution_instrument_id!=m.instrument_id:continue
            bars=load_bars(s,m.instrument_id,now)
            analysis_config=type(config).model_validate(plan.payload["config"]).analysis if plan else config.analysis
            inst=s.get(Instrument,m.instrument_id)
            tick=inst.spec.get("tick_size") or str(D(10)**(-int(inst.spec.get("price_precision",8))))
            assessments=[technical(bars.get(r,[])[-(156 if r=="1W" else 200):],r,now,
                tick,analysis_config) for r in analysis_config.timeframes]
            entry=e.get("entry_zone",[None,None])[1]
            if not entry or not e.get("protection"):continue
            opportunity=choose_targets(assessments,entry,e.get("box"),m.price,bool(e.get("confirmation")))
            template=e.get("template") or ("confirmed_breakout" if e.get("confirmation") else "early_breakout")
            if not plan:
                payload={"template":template,"evidence":e,"opportunity":opportunity,"config":config.model_dump(),
                  "config_version":s.scalar(select(SettingsVersion.id).where(SettingsVersion.state=="published").order_by(SettingsVersion.id.desc()).limit(1)),
                  "created_at":now,"original_protection":e["protection"],"original_t1":opportunity["t1"],
                  "first_signal_at":campaign.first_seen_at,"first_signal_price":str(campaign.first_seen_price),
                  "rule_version":VERSION,"validation_status":"unvalidated"}
                plan=Plan(id=plan_id,episode_id=episode.id,execution_instrument_id=m.instrument_id,revision=1,expires_at=e["expires_at"],payload=payload)
                s.add(plan);s.flush()
            frozen=plan.payload
            # Freeze each plan's risk/targets/config; new snapshots track management separately.
            original=frozen["evidence"];target=frozen["original_t1"]
            config_at_plan=type(config).model_validate(frozen["config"])
            book_entry=s.get(DataCapability,f"{m.venue}:execution_book:{m.instrument_id}")
            book=book_entry.payload if book_entry and book_entry.status=="fresh" and book_entry.expires_at>=now else None
            quote={"price":book["asks"][0][0],"price_kind":"ask","as_of":book.get("observed_at",book["received_at"])} if book and book.get("asks") else None
            cost=None
            if target and not frozen["opportunity"].get("entry_obstructions"):
                ec=config_at_plan.execution
                from engine.pd2.providers import quote_usd_rate
                fx=quote_usd_rate(m.quote_asset,s,now)
                cost=execution_cost(original["entry_zone"][1],original["protection"],target["low"],book,now,
                   str(D(str(ec.probe_notional_usd))/fx) if fx else "0",ec.taker_fee_rates.get(m.venue),ec.funding_scenario_rate,
                   str(ec.early_min_net_r if template=="early_breakout" else ec.confirmed_min_net_r),str(ec.max_spread_bps),ec.depth_max_age_seconds*1000) if fx else {"status":"fx_missing","net_r":None,"reasons":["测试金额USD缺少同期报价币汇率"]}
                cost.update(probe_notional_usd=str(ec.probe_notional_usd),quote_asset=m.quote_asset,quote_usd_rate=str(fx) if fx else None)
            elif target:cost={"status":"entry_obstructed","net_r":None,"reasons":["参与区正在测试既有阻力，等待确认突破"]}
            else:cost={"status":"target_missing","net_r":None,"reasons":["没有可用近目标，无法计算盈亏比"]}
            latest=s.scalar(select(SignalSnapshot).where(SignalSnapshot.plan_id==plan.id).order_by(SignalSnapshot.as_of.desc()).limit(1))
            previous=latest.payload if latest else {}
            protection=previous.get("current_protection") or original["protection"]
            if config_at_plan.analysis.trailing_mode=="chandelier":
                protection=trailing_protection(bars["240"],protection,original["protection"],now,
                    config_at_plan.analysis.trailing_period,str(config_at_plan.analysis.trailing_atr_multiple))
            phase=episode.phase
            if previous.get("action",{}).get("code")=="ended":phase="invalidated"
            if m.price is not None and m.quality.status=="fresh" and 0<=now-m.as_of<=15000 and m.price<=D(protection):phase="invalidated"
            if phase not in ("invalidated","ended") and episode.rule_version==VERSION:
                recent_closed=[b for b in closed(bars["240"],now) if b["close_time"]>original["trigger"]["close_time"]]
                if any(D(b["close"])<D(original["box"]["upper"]) for b in recent_closed):phase="invalidated"
            current_e={**original,"protection":protection,"reason":e.get("reason",original.get("reason"))}
            required_tfs={"60","240"}
            stale_required=[tf for tf in required_tfs if not any(a["timeframe"]==tf and a["status"]=="fresh" for a in assessments)]
            if stale_required:quote=None
            action=decide(current_e,phase,quote,now,cost,config_at_plan.analysis.style,config_at_plan.execution.quote_max_age_seconds*1000)
            sizing=plan_risk(portfolio_risk,original["entry_zone"][1],protection,cost)
            if action["code"] in ("participate","trial") and sizing["status"] in ("risk_exhausted","incomplete"):
                action={**action,"code":"paused","label":"暂停新增仓位","reason":"研究账户风险额度用尽或已有仓位风险不完整","reason_codes":[*action.get("reason_codes",[]),"portfolio_risk"]}
            if phase=="invalidated":action["action_existing"]="保护已触发，退出旧计划"
            frozen_t1=dict(frozen["original_t1"]) if frozen["original_t1"] else None
            if frozen_t1 and m.price and m.price>0:
                frozen_t1.update(remaining_upside_pct=str((D(frozen_t1["low"])/m.price-1)*100),price_multiple=str(D(frozen_t1["low"])/m.price))
            opportunity={**opportunity,"t1":frozen_t1}
            from engine.pd2.opportunity import enrich
            opportunity=enrich(opportunity,assessments,bars,m.price,now,m.quote_asset,action)
            action["action_existing"]="退出旧计划" if action["code"]=="ended" else opportunity["position_management"]
            thesis=headline(action,assessments,opportunity,current_e)
            from engine.pd2.derivatives import context_for
            derivative_context=context_for(m,now)
            if derivative_context.get("cross_market_oi"):
                from engine.pd2.oi_aggregate import record_snapshot
                record_snapshot(s,m.asset_id,derivative_context["cross_market_oi"],now)
            snap={"plan_id":plan.id,"episode_id":episode.id,"instrument_id":m.instrument_id,"asset_id":m.asset_id,
              "rule_version":VERSION,"config_version":frozen["config_version"],"as_of":now,"known_at":now,
              "phase":phase,"phase_label":PHASES[phase],"position_sizing":sizing,"action":action,"trade_thesis":thesis,"evidence":current_e,
              "valid_until":min(now+90000,(quote.get("as_of",now) if quote else now)+config_at_plan.execution.quote_max_age_seconds*1000),"execution":cost,"timeframe_assessments":assessments,"opportunity_plan":opportunity,
              "original_opportunity":frozen["opportunity"],"candles_at_snapshot":bars,"derivative_history_at_snapshot":derivative_history(m.instrument_id,now),"original_protection":original["protection"],
              "current_protection":protection,"first_seen_at":campaign.first_seen_at,"first_seen_price":str(campaign.first_seen_price),
              "market":m.model_dump(mode="json"),"market_comparison":(selection.payload if (selection:=s.get(UserPreference,"main_market:"+m.asset_id)) else {"selected":m.instrument_id,"status":"comparison_pending"}),"validation_status":"unvalidated","probability":None,
              "weekly_cross_gate":weekly_cross_gate(bars.get("1W",[]),now),"derivatives":derivative_context}
            snap["id"]=plan.id+":"+str(now)
            if previous and now-latest.as_of<15000 and action==previous.get("action") and phase==previous.get("phase"):
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
        s.merge(WorkerHeartbeat(role="decisions",updated_at=now,state="ok",details={"snapshots":count,"rule_version":VERSION}))
    return {"snapshots":count}

def decision_list(view="stages"):
    now=now_ms();result=[];seen=set();current_markets={m.instrument_id:m for m in list_markets().rows}
    with Session() as s:
        q=select(SignalSnapshot).distinct(SignalSnapshot.plan_id).order_by(SignalSnapshot.plan_id,SignalSnapshot.as_of.desc())
        latest=list(s.scalars(q))
        # Preserve all plan records, select the current viable plan for each asset.
        priority={"participate":0,"trial":1,"waiting":2,"paused":3,"no_chase":4,"expired":5,"ended":6}
        latest.sort(key=lambda r:(priority.get(r.payload["action"]["code"] if 0<=now-r.as_of<=90000 and (r.payload["action"]["code"] not in ("participate","trial") or now<=r.payload.get("valid_until",r.as_of+15000)) else "paused",9),-r.as_of))
        for snapshot in latest:
            p=snapshot.payload;asset=p["asset_id"]
            if asset in seen or p["instrument_id"] not in current_markets:continue
            seen.add(asset);fresh=0<=now-p["as_of"]<=90000 and (p["action"]["code"] not in ("participate","trial") or now<=p.get("valid_until",p["as_of"]+15000))
            action=p["action"] if fresh or p["action"]["code"]=="ended" else {"code":"paused","label":"数据暂停","reason":"报价或决策已过期，等待后台重新确认"}
            result.append({"id":p["episode_id"],"snapshot_id":snapshot.id,"plan_id":p["plan_id"],
              "asset_id":asset,"instrument_id":p["instrument_id"],"phase":p["phase"],"phase_label":p["phase_label"],
              "action":action,"first_seen_at":p["first_seen_at"],"first_seen_price":p["first_seen_price"],
              "since_first_pct":str((D(p["market"]["price"])/D(p["first_seen_price"])-1)*100) if fresh and p["market"].get("price") else None,
              "fresh":fresh,"market":p["market"],"evidence":p["evidence"],"trade_thesis":p["trade_thesis"] if fresh else {**p["trade_thesis"],"headline":"暂停参与；报价或决策已过期，原计划保护继续"},
              "opportunity_plan":p["opportunity_plan"],"derivatives":p.get("derivatives"),"execution":p["execution"],"timeframe_assessments":p["timeframe_assessments"]})
        # Pending structures remain visible before a plan can exist.
        for episode,campaign in s.execute(select(Episode,Campaign).join(Campaign).order_by(Campaign.first_seen_at.desc())):
            if campaign.asset_id in seen or episode.evidence.get("trigger"):continue
            market=s.get(MarketState,campaign.reference_instrument_id)
            if not market or campaign.reference_instrument_id not in current_markets:continue
            display_market=current_markets[campaign.reference_instrument_id]
            seen.add(campaign.asset_id)
            inst=s.get(Instrument,campaign.reference_instrument_id)
            result.append({"id":episode.id,"asset_id":campaign.asset_id,"instrument_id":inst.id,
              "phase":episode.phase,"phase_label":PHASES[episode.phase],
              "action":{"code":"waiting" if episode.phase=="structure_pending" else "ended","label":"等待突破" if episode.phase=="structure_pending" else "停止旧结构","reason":episode.evidence.get("reason")},
              "first_seen_at":campaign.first_seen_at,"first_seen_price":str(campaign.first_seen_price),"since_first_pct":None,
              "fresh":0<=now-market.as_of<=180000,"market":display_market.model_dump(mode="json"),"evidence":episode.evidence})
        heartbeat=s.get(WorkerHeartbeat,"signals")
        scan={**(heartbeat.details if heartbeat else {}),"as_of":heartbeat.updated_at if heartbeat else None}
    all_rows=result
    if view in ("recommended","trial"):
        desired="trial" if view=="trial" else "participate"
        result=[r for r in result if r["action"]["code"]==desired]
    result.sort(key=lambda r:(priority.get(r["action"]["code"],9),-r["first_seen_at"],r["asset_id"]))
    candidates=[{**r,"blockers":[{"code":r["action"]["code"],"label":r["action"]["reason"]}]} for r in all_rows if r["action"]["code"] not in ("participate","trial")]
    return {"as_of":now,"strategy_status":"pd2_decisions","execution_status":"native_book_cost_gated","view":view,"rows":result,
      "unique_asset_count":len(result),"scan":scan,
      "groups":[{"phase_id":k,"label":v,"asset_count":sum(r["phase"]==k for r in result)} for k,v in PHASES.items()],
      "eligibility":{"status":"enabled","reason":"每个计划按真实报价、原生盘口、费用情景与冻结近目标评估；无合格计划时明确列出原因。",
        "missing_checks":[],"total_events":len(all_rows),"confirmed_events":sum(r["phase"] in ("structure_confirmed","trend_continuing") for r in all_rows),
        "early_events":sum(r["phase"]=="early_trigger" for r in all_rows),"waiting_events":sum(r["phase"]=="structure_pending" for r in all_rows),
        "candidate_count":len(candidates),"candidates":candidates}}

def derivative_history(instrument,before=None,limit=500):
    now=before or now_ms();before=before or now
    with Session() as s:
        inst=s.get(Instrument,instrument)
        if not inst:return None
        oi=list(reversed(list(s.scalars(select(OiPoint).where(OiPoint.instrument_id==instrument,OiPoint.event_time<=before,OiPoint.received_at<=before).order_by(OiPoint.event_time.desc()).limit(limit)))))
        funding=list(reversed(list(s.scalars(select(FundingPoint).join(RawEvent,FundingPoint.raw_ref==RawEvent.id).where(FundingPoint.instrument_id==instrument,FundingPoint.settled_at<=before,RawEvent.received_at<=before).order_by(FundingPoint.settled_at.desc()).limit(limit)))))
        return {"instrument_id":instrument,"quote_asset":inst.quote_asset,"as_of":before,"quantity_unit":inst.spec.get("base_asset",inst.asset_id),
          "oi":[{"time":p.event_time,"quantity":str(p.base_quantity) if p.base_quantity is not None else None,
              "notional_quote":str(p.notional_quote) if p.notional_quote is not None else None,"received_at":p.received_at} for p in oi],
          "funding":[{"time":p.settled_at,"rate":str(p.rate),"interval_seconds":p.interval_seconds,"kind":"settled"} for p in funding],
          "oi_status":"fresh" if oi and now-oi[-1].event_time<=180000 else "stale" if oi else "insufficient_history",
          "funding_status":"available" if funding else "insufficient_history","scope":"single_native_instrument"}

def chart_snapshot(instrument,episode=None,snapshot_id=None):
    with Session() as s:
        snapshot=s.get(SignalSnapshot,snapshot_id) if snapshot_id else s.scalar(select(SignalSnapshot).join(Plan).where(Plan.episode_id==episode,Plan.execution_instrument_id==instrument).order_by(SignalSnapshot.as_of.desc()).limit(1)) if episode else None
        if snapshot and snapshot.payload["instrument_id"]!=instrument:return None
        if snapshot_id and not snapshot:return None
        from engine.pd2.snapshots import hydrate
        payload=hydrate(s,snapshot.payload) if snapshot else None
        if payload and payload["action"]["code"]!="ended" and not snapshot_id and (now_ms()-payload["as_of"]>90000 or (payload["action"]["code"] in ("participate","trial") and now_ms()>payload.get("valid_until",payload["as_of"]+15000))):
            payload["action"]={"code":"paused","label":"暂停参与","reason":"报价或决策已过期，等待重新评估"}
            payload["trade_thesis"]={**payload["trade_thesis"],"headline":"暂停参与；报价或决策已过期，原计划保护继续"}
        return payload
