from engine.repository import write_heartbeat
from decimal import Decimal as D
from time import perf_counter
from threading import Lock
from contextlib import ExitStack
from hashlib import blake2b
from sqlalchemy import select,text,func,cast,BigInteger
from engine.db import Session
from engine.models import Candle,Episode,Campaign,WorkerHeartbeat,Instrument,UserPreference
from engine.repository import list_markets,emit,runtime_config
from engine.adapters.base import now_ms
from engine.contracts import PHASES
from engine.strategies.structures import candidate,evaluate,closed,target,VERSION
from engine.pd2.analysis import technical

RECONCILE_BATCH_SIZE=25
REQUIRED_RESOLUTIONS=("60","240","1D","1W")
_scan_locks=[Lock() for _ in range(256)]

def _reconciliation_batch(limit=RECONCILE_BATCH_SIZE):
    from engine.pd2.universe_policy import membership,primary_eligible
    with Session() as s:
        aliases,admitted=membership(s)
        ids=sorted(i.id for i in s.scalars(select(Instrument))
            if i.venue in ("binance","binance_spot","binance_alpha","wallet_rising")
            and primary_eligible(i) and aliases.get(i.asset_id,i.asset_id) in admitted)
        row=s.get(UserPreference,"structure_reconcile_cursor")
        cursor=(row.payload if row else {}).get("instrument_id")
    start=next((n for n,value in enumerate(ids) if cursor is None or value>cursor),len(ids))
    if start>=len(ids):start=0
    batch=ids[start:start+limit]
    return batch,start+len(batch)>=len(ids),len(ids)

def _recent_bars(session,instrument_ids,resolutions,limit):
    if not instrument_ids:return {}
    from engine.p38.windows import read
    result={};at=now_ms()
    for iid in instrument_ids:
        for tf in resolutions:
            result[(iid,tf)]=[{k:str(v) if isinstance(v,D) else v for k,v in row.items()} for row in read(session,iid,tf,at,limit)]
    return result

def scan_signals(instrument_ids=None,prepared=None):
    reconcile_info=None
    if instrument_ids is None:
        instrument_ids,completed,total=_reconciliation_batch()
        reconcile_info=(completed,total)
    ids=list(instrument_ids);totals={}
    for start in range(0,len(ids)):
        batch=ids[start:start+1]
        # Acquire market locks before opening DB sessions; unrelated coins proceed.
        indices=sorted({int.from_bytes(blake2b(str(i).encode(),digest_size=2).digest(),'big')%256 for i in batch})
        with ExitStack() as stack:
            for index in indices:stack.enter_context(_scan_locks[index])
            info=(reconcile_info[0] and start==len(ids)-1,reconcile_info[1]) if reconcile_info is not None else None
            part=_scan_signals(batch,info,prepared=prepared) if prepared is not None else _scan_signals(batch,info) if info is not None else _scan_signals(batch)
        for key,value in part.items():totals[key]=totals.get(key,0)+value
    return totals


def prepare_scan(instrument_ids):
    """Detached inputs/recognition; callers borrow a mutation fence afterwards."""
    now=now_ms();data=list_markets(instrument_ids,light=True);config=runtime_config()
    markets=[m for m in data.rows if m.in_universe and m.venue in ("binance","binance_spot","binance_alpha","wallet_rising")]
    # Read and calculate reusable indicators before acquiring a market lock.
    read_at=perf_counter();market_ids=[m.instrument_id for m in markets]
    with Session() as read:
        bars=_recent_bars(read,market_ids,REQUIRED_RESOLUTIONS,300)
        instruments={row.id:row for row in read.scalars(select(Instrument).where(Instrument.id.in_(market_ids)))}
    read_ms=(perf_counter()-read_at)*1000;compute_at=perf_counter();summaries_by_market={}
    for m in markets:
        inst=instruments[m.instrument_id];precision=inst.spec.get('price_precision')
        tick=inst.spec.get('tick_size') or (str(D(10)**(-int(precision))) if precision is not None else '0.00000001')
        summaries=[]
        for resolution in REQUIRED_RESOLUTIONS:
            assessment=technical(bars.get((m.instrument_id,resolution),[]),resolution,now,tick,config.analysis)
            for key in ('series','levels','excluded_levels','fibonacci'):assessment.pop(key,None)
            summaries.append(assessment)
        summaries_by_market[m.instrument_id]=summaries
    from engine.strategies.p33_runtime import prepare_recognition
    prepared_recognition={}
    for m in markets:
        try:
            prepared_recognition[m.instrument_id]=prepare_recognition(instruments[m.instrument_id],
                {tf:bars.get((m.instrument_id,tf),[]) for tf in REQUIRED_RESOLUTIONS},now,m.model_dump(mode='json'))
        except Exception:
            # Preserve the existing observer's bounded fallback/error record.
            # Preparation failure cannot discard the prior plan or signal.
            import logging
            logging.getLogger('erji.pipeline').exception('recognition preparation failed instrument=%s',m.instrument_id)
    return dict(ids=list(instrument_ids),now=now,markets=markets,config=config,bars=bars,instruments=instruments,
        summaries_by_market=summaries_by_market,prepared_recognition=prepared_recognition,
        read_ms=read_ms,compute_ms=(perf_counter()-compute_at)*1000)


def _scan_signals(instrument_ids=None,reconcile_info=None,prepared=None):
    if instrument_ids is None or len(instrument_ids)>1:return scan_signals(instrument_ids)
    started=perf_counter();reconcile=reconcile_info is not None
    completed,eligible_count=reconcile_info if reconcile_info is not None else (False,None)
    prepared=prepared if prepared is not None and prepared['ids']==list(instrument_ids) else prepare_scan(instrument_ids)
    now=prepared['now'];markets=prepared['markets'];config=prepared['config'];bars=prepared['bars'];instruments=prepared['instruments']
    summaries_by_market=prepared['summaries_by_market'];prepared_recognition=prepared['prepared_recognition']
    read_ms=prepared['read_ms'];compute_ms=prepared['compute_ms'];commit_at=perf_counter()
    market_ids=[m.instrument_id for m in markets]
    counts={"scanned":0,"matched":0,"stale":0,"quote_stale":0,"candles_stale":0,"candle_gaps":0,"shadow_errors":0}
    with Session.begin() as s:
        s.info['deferred_signal_events']=[]
        admission_ms=s.info.get('admission_ms',0)
        lock_at=perf_counter()
        for iid in sorted(m.instrument_id for m in markets):
            s.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),{"key":"structure:"+iid})
        lock_ms=(perf_counter()-lock_at)*1000
        current=_recent_bars(s,market_ids,REQUIRED_RESOLUTIONS,300)
        if current!=bars:return {**counts,'superseded':len(markets)}
        if any((s.get(Instrument,iid) is None or s.get(Instrument,iid).spec!=instruments[iid].spec) for iid in market_ids):
            return {**counts,'superseded':len(markets)}
        for m in markets:
            newest=s.scalar(select(func.max(cast(Episode.evidence['evaluated_at'].as_string(),BigInteger))).join(Campaign).where(Campaign.reference_instrument_id==m.instrument_id))
            if newest and newest>now:counts['superseded']=counts.get('superseded',0)+1;continue
            # Recheck old pending candidates even if today's quote is stale.
            # Historical triggered plans and their frozen evidence are not rewritten.
            from engine.pd2.box_quality import audit as audit_box
            pending=list(s.scalars(select(Episode).join(Campaign).where(
                Campaign.reference_instrument_id==m.instrument_id,
                Episode.rule_version==VERSION,Episode.phase=='structure_pending')))
            for old_candidate in pending:
                if old_candidate.evidence.get('trigger'):continue
                quality=audit_box(old_candidate.evidence.get('box') or {},bars.get((m.instrument_id,'240'),[]))
                if quality['status']=='rejected':
                    old_candidate.phase='ended'
                    old_candidate.evidence={**old_candidate.evidence,'candidate_quality':quality,
                        'phase':'ended','reason':quality['reason'],'evaluated_at':now,'phase_changed_at':now,
                        'transitions':[*old_candidate.evidence.get('transitions',[]),
                            {'from':'structure_pending','to':'ended','at':now,'reason':quality['reason'],'audit_version':quality['version']}]}
                    emit(s,'signal_update',{'asset_id':m.asset_id,'episode_id':old_candidate.id,'phase':'ended'})
            from engine.pd2.p35_schedule import background
            background(s,instruments[m.instrument_id],{tf:bars.get((m.instrument_id,tf),[]) for tf in REQUIRED_RESOLUTIONS},now,config.analysis,
                prepared={a['timeframe']:a for a in summaries_by_market[m.instrument_id]} if instruments[m.instrument_id].spec.get('tick_size') else None)
            from engine.strategies.p33_runtime import observe
            queued_before=len(s.info['deferred_signal_events'])
            try:
                with s.begin_nested():
                    p33_result=observe(s,instruments[m.instrument_id],{tf:bars.get((m.instrument_id,tf),[]) for tf in REQUIRED_RESOLUTIONS},now,market=m.model_dump(mode="json"),execution_config=config.execution,
                        prepared=prepared_recognition.get(m.instrument_id))
                    counts['p33_analyzed']=counts.get('p33_analyzed',0)+1
                    counts['p33_waiting']=counts.get('p33_waiting',0)+p33_result.get('statuses',{}).get('waiting',0)
                    counts['p33_missing_inputs']=counts.get('p33_missing_inputs',0)+p33_result.get('statuses',{}).get('missing_data',0)
                    counts['p33_ready']=counts.get('p33_ready',0)+sum(n for status,n in p33_result.get('statuses',{}).items() if status!='missing_data')
                    counts['p33_triggered']=counts.get('p33_triggered',0)+p33_result.get('statuses',{}).get('triggered',0)
            except Exception as error:
                del s.info['deferred_signal_events'][queued_before:]
                # A research observer failure must not stop the existing box engine.
                counts['shadow_errors']+=1
                s.merge(UserPreference(key='p33_shadow_error:'+m.instrument_id,updated_at=now,
                    payload={'status':'error','reason':type(error).__name__,'trading_enabled':False}))
            if m.quality.status!="fresh":counts["stale"]+=1;counts["quote_stale"]+=1;continue
            four=bars.get((m.instrument_id,"240"),[]);one=bars.get((m.instrument_id,"60"),[])
            if not four or now-four[-1]["close_time"]>15000_000 or not one or now-one[-1]["close_time"]>4200_000:
                counts["stale"]+=1;counts["candles_stale"]+=1;continue
            recent=four[-80:];recent_one=one[-100:]
            if any(b["open_time"]-a["open_time"]!=14400_000 for a,b in zip(recent,recent[1:])) or any(b["open_time"]-a["open_time"]!=3600_000 for a,b in zip(recent_one,recent_one[1:])):
                counts["stale"]+=1;counts["candle_gaps"]+=1;continue
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
                if evidence.get("trigger") and evidence.get("protection") and 0<=now-m.as_of<=90000 and D(m.price)<=D(evidence["protection"]):
                    # Mark is a risk check, not proof of an executed order.
                    state.update(phase="invalidated",reason="标记价格已触及冻结保护价，研究计划撤销",invalidated_at=now)
                evidence={**evidence,**state,"evaluated_at":now,
                    "structure_as_of":max(four[-1]["close_time"],one[-1]["close_time"]),
                    "structure_valid_until":one[-1]["close_time"]+4_200_000}
                from engine.p39.pipeline import origin
                evidence['latest_analysis']=origin()
                evidence['required_inputs']={'60':'closed_price_and_quote_volume','240':'closed_structure_and_quote_volume'}
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
                evidence={**state,"instrument_id":m.instrument_id,"detected_at":now,"evaluated_at":now,
                    "structure_as_of":max(four[-1]["close_time"],one[-1]["close_time"]),
                    "structure_valid_until":one[-1]["close_time"]+4_200_000,
                    "phase_changed_at":now,"transitions":[],"initial_detection":"live_scan"}
                from engine.p39.pipeline import origin
                evidence['origin']=origin();evidence['latest_analysis']=origin()
                active=Episode(id=state["id"],campaign_id=campaign_id,strategy_id="box_breakout_research",phase=state["phase"],rule_version=VERSION,evidence=evidence)
                s.add(active)
                emit(s,"signal_update",{"asset_id":m.asset_id,"episode_id":active.id,"phase":active.phase})
            if any(key not in evidence for key in ("targets_4h","targets_1d","targets_1w")) or (evidence.get("trigger") and not evidence.get("targets_frozen_at")):
                entry=D(evidence["entry_zone"][1]) if evidence.get("entry_zone") else D(evidence["box"]["trigger_price"])
                evidence["targets_4h"]=target(closed(bars.get((m.instrument_id,"240"),[]),now),entry,"4H")
                evidence["targets_1d"]=target(closed(bars.get((m.instrument_id,"1D"),[]),now),entry,"1D")
                evidence["targets_1w"]=target(closed(bars.get((m.instrument_id,"1W"),[]),now),entry,"1W")
                if evidence.get("trigger"):evidence["targets_frozen_at"]=now
            latest_closes={resolution:(bars.get((m.instrument_id,resolution)) or [{}])[-1].get("close_time") for resolution in REQUIRED_RESOLUTIONS}
            prior={item.get("timeframe"):item for item in evidence.get("timeframe_assessments",[])}
            if any(prior.get(resolution,{}).get("closed_at")!=closed_at for resolution,closed_at in latest_closes.items()):
                evidence["timeframe_assessments"]=summaries_by_market[m.instrument_id]
            evidence['required_inputs']={'60':'closed_price_and_quote_volume','240':'closed_structure_and_quote_volume'}
            active.evidence=evidence
            counts["matched"]+=1
        elapsed_ms=round((perf_counter()-started)*1000,1)
        old=s.get(WorkerHeartbeat,'signals');previous=old.details if old else {}
        if reconcile:
            cursor=s.get(UserPreference,"structure_reconcile_cursor")
            prior=(cursor.payload if cursor else {}).get("cycle_counts",{"scanned":0,"matched":0,"stale":0})
            cycle={key:prior.get(key,0)+counts[key] for key in counts}
            if completed:
                summary={**previous,**cycle,"last_full_scan_at":now,"reconcile_cycle_complete":True,
                    "eligible_markets":eligible_count,"last_batch_ms":elapsed_ms,"batch_size":len(instrument_ids)}
                cursor_payload={"instrument_id":None,"cycle_counts":{"scanned":0,"matched":0,"stale":0}}
            else:
                summary={**previous,"last_reconcile_batch_at":now,"reconcile_cycle_complete":False,
                    "eligible_markets":eligible_count,"last_batch_ms":elapsed_ms,"batch_size":len(instrument_ids)}
                cursor_payload={"instrument_id":instrument_ids[-1] if instrument_ids else None,"cycle_counts":cycle}
            s.merge(UserPreference(key="structure_reconcile_cursor",updated_at=now,payload=cursor_payload))
        else:
            from engine.p40.diagnostics import scan_view
            summary={**scan_view(previous),'last_event_scan_at':now,'last_event_markets':counts['scanned'],
                'last_event_matches':counts['matched'],'last_event_ms':elapsed_ms,
                'event_scan':{'at':now,'requested_markets':len(instrument_ids),'counts':dict(counts),
                    'reason':'batch_error' if counts['shadow_errors'] else 'stale_skip' if counts['stale'] else 'ok',
                    'elapsed_ms':elapsed_ms,'scope':'native instrument event scan'}}
        write_heartbeat(s,"signals",now,"ok",{**summary,"rule_version":VERSION,"mode":"structure_signals","entry_execution":"delegated_pd2_frozen_plan"})
        for payload in s.info.pop('deferred_signal_events'):
            emit(s,'signal_update',payload)
    import logging
    logging.getLogger('erji.pipeline').info('structure stages markets=%s db_admission_ms=%.1f read_ms=%.1f compute_ms=%.1f lock_wait_ms=%.1f commit_ms=%.1f total_ms=%.1f',len(markets),admission_ms,read_ms,compute_ms,lock_ms,(perf_counter()-commit_at)*1000,(perf_counter()-started)*1000)
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
