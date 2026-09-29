from decimal import Decimal as D
from sqlalchemy import select,text,func
from engine.db import Session
from engine.models import Candle,Episode,Campaign,WorkerHeartbeat
from engine.repository import list_markets,emit
from engine.adapters.base import now_ms
from engine.contracts import PHASES
from engine.strategies.structures import candidate,evaluate,closed,target,VERSION

def scan_signals(instrument_ids=None):
    now=now_ms();data=list_markets(instrument_ids,light=True)
    markets=[m for m in data.rows if m.candles_count>=60 and m.in_universe and m.venue in ("binance","binance_spot","binance_alpha","wallet_rising")]
    counts={"scanned":0,"matched":0,"stale":0}
    with Session.begin() as s:
        s.execute(text("SELECT pg_advisory_xact_lock(1877103)"))
        bars={}
        ranked=select(Candle.instrument_id,Candle.resolution,Candle.open_time,func.row_number().over(partition_by=(Candle.instrument_id,Candle.resolution),order_by=Candle.open_time.desc()).label('rn')).where(Candle.instrument_id.in_([m.instrument_id for m in markets]),Candle.is_closed==True).subquery()
        recent=select(Candle).join(ranked,(Candle.instrument_id==ranked.c.instrument_id)&(Candle.resolution==ranked.c.resolution)&(Candle.open_time==ranked.c.open_time)).where(ranked.c.rn<=300).order_by(Candle.open_time)
        for c in s.scalars(recent):
            bars.setdefault((c.instrument_id,c.resolution),[]).append({"open_time":c.open_time,"close_time":c.close_time,"is_closed":c.is_closed,
                "open":str(c.open),"high":str(c.high),"low":str(c.low),"close":str(c.close),"quote_volume":str(c.quote_volume) if c.quote_volume is not None else None})
        for m in markets:
            if m.quality.status!="fresh":counts["stale"]+=1;continue
            four=bars.get((m.instrument_id,"240"),[])[-300:];one=bars.get((m.instrument_id,"60"),[])[-300:]
            if not four or now-four[-1]["close_time"]>15000_000 or not one or now-one[-1]["close_time"]>4200_000:
                counts["stale"]+=1;continue
            recent=four[-80:];recent_one=one[-100:]
            if any(b["open_time"]-a["open_time"]!=14400_000 for a,b in zip(recent,recent[1:])) or any(b["open_time"]-a["open_time"]!=3600_000 for a,b in zip(recent_one,recent_one[1:])):
                counts["stale"]+=1;continue
            counts["scanned"]+=1
            active=s.scalar(select(Episode).join(Campaign).where(Campaign.asset_id==m.asset_id,Campaign.reference_instrument_id==m.instrument_id,
                Episode.rule_version==VERSION,Episode.phase.notin_(["invalidated","ended"])).order_by(Campaign.first_seen_at.desc()).limit(1))
            if active:
                evidence=active.evidence;state=evaluate(evidence["box"],four,one,now,{**evidence,"phase":active.phase})
                previous=active.phase
                # A confirmed trend low is stored and can trigger weakening before it is replaced.
                prior_low=evidence.get("confirmed_low")
                if prior_low and state["phase"] not in ("invalidated","ended") and D(four[-1]["close"])<D(prior_low):
                    state.update(phase="weakening",reason="4H 收盘跌破之前已确认的抬高低点")
                if evidence.get("trigger") and evidence.get("protection") and 0<=now-m.as_of<=15000 and D(m.price)<=D(evidence["protection"]):
                    # Mark is a risk check, not proof of an executed order.
                    state.update(phase="invalidated",reason="标记价格已触及冻结保护价，研究计划撤销",invalidated_at=now)
                evidence={**evidence,**state,"evaluated_at":now}
                active.phase=state["phase"]
                if previous!=active.phase:
                    evidence["phase_changed_at"]=now
                    evidence["transitions"]=[*evidence.get("transitions",[]),{"from":previous,"to":active.phase,"at":now,"reason":state["reason"]}]
                    emit(s,"signal_update",{"asset_id":m.asset_id,"episode_id":active.id,"phase":active.phase})
            else:
                state=candidate(m.instrument_id,four,one,now)
                if not state or s.get(Episode,state["id"]):continue
                campaign=s.scalar(select(Campaign).where(Campaign.asset_id==m.asset_id,Campaign.reference_instrument_id==m.instrument_id,Campaign.state=="active").order_by(Campaign.first_seen_at.desc()).limit(1))
                if not campaign:
                    campaign=Campaign(id="campaign-"+state["id"],asset_id=m.asset_id,first_seen_at=now,first_seen_price=m.price,reference_instrument_id=m.instrument_id,state="active")
                    s.add(campaign);s.flush()
                campaign_id=campaign.id
                evidence={**state,"instrument_id":m.instrument_id,"detected_at":now,"evaluated_at":now,"phase_changed_at":now,"transitions":[],"initial_detection":"live_scan"}
                active=Episode(id=state["id"],campaign_id=campaign_id,strategy_id="box_breakout_research",phase=state["phase"],rule_version=VERSION,evidence=evidence)
                s.add(active)
                emit(s,"signal_update",{"asset_id":m.asset_id,"episode_id":active.id,"phase":active.phase})
            if "targets_1d" not in evidence or (evidence.get("trigger") and not evidence.get("targets_frozen_at")):
                entry=D(evidence["entry_zone"][1]) if evidence.get("entry_zone") else D(evidence["box"]["trigger_price"])
                evidence["targets_1d"]=target(closed(bars.get((m.instrument_id,"1D"),[])[-300:],now),entry)
                evidence["targets_1w"]=target(closed(bars.get((m.instrument_id,"1W"),[])[-300:],now),entry)
                if evidence.get("trigger"):evidence["targets_frozen_at"]=now
            active.evidence=evidence
            counts["matched"]+=1
        old=s.get(WorkerHeartbeat,'signals')
        previous=old.details if old else {}
        summary={**counts,'last_full_scan_at':now} if instrument_ids is None else {**previous,'last_event_scan_at':now,'last_event_markets':counts['scanned'],'last_event_matches':counts['matched']}
        s.merge(WorkerHeartbeat(role="signals",updated_at=now,state="ok",details={**summary,"rule_version":VERSION,"mode":"structure_signals","entry_execution":"delegated_pd2_frozen_plan"}))
    return counts

def action_for(evidence,phase,price,fresh):
    if phase in ("invalidated","ended"):return {"code":"ended","label":"停止旧计划","reason":evidence["reason"]}
    if not fresh:return {"code":"paused","label":"数据暂停","reason":"必需行情或扫描结果已过期"}
    if phase=="weakening":return {"code":"paused","label":"暂停新参与","reason":evidence["reason"]}
    if phase=="structure_pending":
        if D(price)<D(evidence["box"]["lower"]):
            return {"code":"waiting","label":"箱底下方 · 待收盘","reason":"现价已低于箱体下沿，等待 4H 收盘确认是否失效，暂停新参与"}
        return {"code":"waiting","label":"等待突破","reason":"需闭合 K 线与成交增强同时满足"}
    zone=evidence.get("entry_zone")
    if phase not in ("early_trigger","structure_confirmed","trend_continuing") or not zone or not evidence.get("protection") or not evidence.get("expires_at"):
        return {"code":"paused","label":"计划数据不完整","reason":"参与带、保护价或有效期缺失，无法发布建议"}
    if zone and D(price)>D(zone[1]):return {"code":"no_chase","label":"不建议追入","reason":"现价超过本次参与带，等待独立回踩计划"}
    if evidence.get("expires_at",0)<now_ms():return {"code":"expired","label":"等待新机会","reason":"原入场窗口已到期，趋势继续独立跟踪"}
    if zone and D(price)<D(zone[0]):return {"code":"waiting","label":"等待回到参与区","reason":"现价不在冻结参与带内"}
    return {"code":"execution_pending","label":"已在参与区 · 待交易校验","reason":"结构已触发且现价在原参与区内；盘口、手续费与扣费后盈亏比尚未接入，建议发布未启用"}

def signal_list(view="stages"):
    now=now_ms();market_data=list_markets();markets={m.instrument_id:m for m in market_data.rows};result=[];seen=set()
    with Session() as s:
        heartbeat=s.get(WorkerHeartbeat,"signals")
        episodes=list(s.execute(select(Episode,Campaign).join(Campaign).where(Episode.rule_version==VERSION)))
        episodes.sort(key=lambda pair:pair[0].evidence.get("detected_at",0),reverse=True)
        for episode,campaign in episodes:
            e=episode.evidence;m=markets.get(campaign.reference_instrument_id)
            if not m or campaign.asset_id in seen:continue
            seen.add(campaign.asset_id)
            fresh=m.quality.status=="fresh" and 0<=now-e.get("evaluated_at",0)<180_000
            action=action_for(e,episode.phase,m.price,fresh)
            result.append({"id":episode.id,"asset_id":campaign.asset_id,"instrument_id":m.instrument_id,"phase":episode.phase,"phase_label":PHASES[episode.phase],
                "action":action,"first_seen_at":campaign.first_seen_at,"first_seen_price":str(campaign.first_seen_price),
                "since_first_pct":str((m.price/campaign.first_seen_price-1)*100) if fresh and campaign.first_seen_price else None,
                "fresh":fresh,"market":m.model_dump(mode="json"),"evidence":e})
        diagnostics=heartbeat.details if heartbeat else {};scan_time=heartbeat.updated_at if heartbeat else None
    return signal_payload(result,view,now,{**diagnostics,"as_of":scan_time})


def signal_payload(rows,view,now,scan):
    """Keep real recommendations separate from structures awaiting execution checks."""
    wanted="trial" if view=="trial" else "participate"
    relevant_phases=("early_trigger",) if view=="trial" else ("structure_confirmed","trend_continuing")
    candidates=[]
    for row in rows:
        if row["phase"] not in relevant_phases or row["action"]["code"] in ("participate","trial"):continue
        e=row["evidence"];blockers=[];zone=e.get("entry_zone");price=row["market"].get("price")
        if not row["fresh"]:blockers.append({"code":"stale","label":"数据待更新"})
        if not zone or not e.get("protection") or not e.get("expires_at"):
            blockers.append({"code":"incomplete","label":"计划点位或有效期缺失"})
        else:
            if price is None: blockers.append({"code":"missing_price","label":"当前报价缺失"})
            elif D(price)>D(zone[1]):blockers.append({"code":"above_zone","label":"已超参与区，不追入"})
            elif D(price)<D(zone[0]):blockers.append({"code":"below_zone","label":"现价低于参与区"})
            if now>=e["expires_at"]:blockers.append({"code":"expired","label":"原入场窗口已到期"})
        candidates.append({**row,"blockers":blockers})
    candidates.sort(key=lambda r:(bool(r["blockers"]),-r["first_seen_at"]))
    counts={code:sum(r["action"]["code"]==code for r in rows) for code in ("participate","trial")}
    selected=[r for r in rows if r["action"]["code"]==wanted] if view in ("recommended","trial") else rows
    return {"as_of":now,"strategy_status":"structure_signals","execution_status":"cost_and_depth_pending","view":view,"rows":selected,
        "groups":[{"phase_id":k,"label":v,"asset_count":sum(r["phase"]==k for r in selected)} for k,v in PHASES.items()],
        "scan":scan,"unique_asset_count":len(selected),"action_counts":counts,
        "eligibility":{"status":"not_enabled","reason":"建议发布功能尚未完成；当前空列表不能解释为全市场没有机会。",
            "missing_checks":[{"code":"depth","label":"盘口与滑点","detail":"按参与金额确认能否成交，以及成交价格偏差"},
                {"code":"cost","label":"交易成本","detail":"计入买入、退出的手续费及适用资金费用"},
                {"code":"net_r","label":"扣费后盈亏比","detail":"比较到下一压力区的收益与到保护位的风险"}],
            "total_events":len(rows),"confirmed_events":sum(r["phase"] in ("structure_confirmed","trend_continuing") for r in rows),
            "early_events":sum(r["phase"]=="early_trigger" for r in rows),
            "waiting_events":sum(r["phase"]=="structure_pending" for r in rows),
            "candidate_count":len(candidates),"candidates":candidates}}
