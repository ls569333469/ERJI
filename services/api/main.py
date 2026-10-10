import asyncio, json,logging,time
from contextlib import asynccontextmanager
from typing import Literal
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import select, text, func
from engine.config import settings
from engine.db import Session
from engine.models import *
from engine.contracts import *
from engine.repository import bootstrap,active_config,list_markets,read_events,emit
from engine.adapters.base import now_ms

class EventHub:
    def __init__(self):
        self.queues=set();self.cursor=0;self.last_error=None;self.error_at=None;self.last_success_at=None;self.recovered_at=None;self.logged_at=0;self.failing=False
    def status(self):
        return {'subscribers':len(self.queues),'cursor':self.cursor,'last_error':self.last_error,'error_at':self.error_at,'last_success_at':self.last_success_at,'recovered_at':self.recovered_at,'failing':self.failing}
    async def start(self):
        # New connections read their initial list over HTTP. Historic replay is
        # bounded in /stream; the shared pump must not replay the entire database.
        def latest():
            with Session() as s:return s.scalar(select(func.max(StreamEvent.id))) or 0
        self.cursor=await asyncio.to_thread(latest);self.last_success_at=now_ms()
    async def pump(self):
        while True:
            try:
                rows=await asyncio.to_thread(read_events,self.cursor)
                for event in rows:
                    self.cursor=event["id"]
                    for queue in list(self.queues):
                        if queue.full():
                            while not queue.empty(): queue.get_nowait()
                            queue.put_nowait({"id":event["id"],"kind":"reset","payload":{}})
                        else: queue.put_nowait(event)
                self.last_success_at=now_ms()
                if self.failing:self.recovered_at=self.last_success_at
                self.failing=False
            except Exception as error:
                self.failing=True;self.last_error=type(error).__name__;self.error_at=now_ms()
                if time.monotonic()-self.logged_at>=60:
                    logging.getLogger('erji.events').warning('event pump failed: %s',self.last_error);self.logged_at=time.monotonic()
            await asyncio.sleep(1)

hub=EventHub()
@asynccontextmanager
async def lifespan(app):
    from engine.p38.startup import manifest
    logging.getLogger('erji.startup').info('startup %s',json.dumps(manifest('api'),ensure_ascii=False))
    if settings().require_auth:
        from engine.pd2.auth import secret
        secret()
    await asyncio.to_thread(bootstrap)
    await hub.start()
    task=asyncio.create_task(hub.pump())
    yield
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass

app=FastAPI(title="ERJI Market Radar",version=SCHEMA_VERSION,lifespan=lifespan)
from urllib.parse import urlparse
app.add_middleware(TrustedHostMiddleware,allowed_hosts=list(set(["127.0.0.1","localhost","api","testserver",urlparse(settings().web_origin).hostname or "localhost"])))

@app.middleware("http")
async def local_writes(request,call_next):
    cfg=settings()
    if cfg.require_auth and request.url.path not in ("/v1/health","/v1/auth/login"):
        from engine.pd2.auth import secret,verify_session
        try:session=verify_session(request.cookies.get("erji_session",""),secret())
        except (OSError,ValueError):session=None
        if not session:return JSONResponse(status_code=401,content={"message":"请登录 ERJI 管理员会话"})
        if request.method in {"POST","PUT","PATCH","DELETE"}:
            import hmac
            if not hmac.compare_digest(request.headers.get("x-erji-csrf",""),session["csrf"]):
                return JSONResponse(status_code=403,content={"message":"会话校验失败，请重新登录"})
    if request.method in {"POST","PATCH","DELETE","PUT"}:
        origin=request.headers.get("origin")
        if request.headers.get("x-erji-client")!="local-ui" or (origin and origin!=settings().web_origin):
            return JSONResponse(status_code=403,content={"error_code":"LOCAL_WRITE_REQUIRED","message":"修改操作须来自本机 ERJI 页面"})
    return await call_next(request)

@app.get("/v1/health")
def health():
    from engine.p40.diagnostics import current_run,run_scope
    run=current_run()
    with Session() as s:
        s.execute(text("SELECT 1"))
        workers=[{"role":w.role,"updated_at":w.updated_at,"state":w.state,"fresh":0<=now_ms()-w.updated_at<300_000,'run_id':w.details.get('run_id'),'pid':w.details.get('pid')} for w in s.scalars(select(WorkerHeartbeat))]
    from engine.pd2.source_policy import REALTIME,VERSION,DISABLED
    previous_run=sum(run_scope(w,run)=='previous_run' for w in workers)
    workers=[{**w,'run_scope':run_scope(w,run)} for w in workers if run_scope(w,run)!='previous_run' and not any(w['role']==p or w['role'].split(':')[0].endswith('_'+p) or ':'+p+':' in w['role'] for p in DISABLED)]
    return {"status":"ok","database":"postgresql","mode":settings().mode,"schema_version":SCHEMA_VERSION,"workers":workers,'run_id':run,'previous_run_workers':previous_run,"strategy_status":"pd2_decisions","execution_status":"native_book_strategy_gated_cost_reference","source_policy":VERSION,"telegram_status":"not_enabled"}

@app.get("/v1/operations")
def operations():
    """Compact database-only proof that collection and analysis are progressing."""
    now=now_ms()
    from engine.p40.diagnostics import current_run,run_scope,scan_view
    run=current_run()
    expected={"analysis_owner":180_000,"anomaly_discovery":180_000,"live_binance":180_000,"signals":120_000,"signal_projection":120_000,"signal_coverage":600_000,
        "stream_binance":120_000,"candles_binance":600_000,"supplements":600_000}
    detail_keys={"updated","gaps","markets","bars_updated","rows","snapshots","scanned","matched",
        "eligible_markets","unique_assets","history_ready_markets","history_pending_or_short","last_batch_ms","by_work_kind","producer","lanes","timing_scope",
        "last_full_scan_at","reconcile_cycle_complete","trade_markets","candidate_subscriptions","run_id","pid","execution_owner","tasks","priority_overflow","write_queue","last_receive_at","close_code","close_reason","database_timings"}
    detail_keys.update({'window_coverage','oi_window_coverage','snapshot_at','event_delivery','scan_started_at','scan_finished_at','scan_in_progress',
        'scan_markets','ordinary_interval_ms','hot_interval_ms','receive_timings','loop_lag','quote_writer','lane_slots','demand_metrics','reason','stage','work_kind','restarts','last_error','last_error_at','next_retry_at','scope','role_name','lifecycle','price_receipts','oi_receipts','oi_persisted_probe','retry_seconds'})
    with Session() as s:
        beats={w.role:w for w in s.scalars(select(WorkerHeartbeat))}
        previous_beats={k:w for k,w in beats.items() if run_scope(w.details,run)=='previous_run'}
        beats={k:w for k,w in beats.items() if k not in previous_beats}
        owner=beats.get("analysis_owner")
        p38_active=bool(owner and 0<=now-owner.updated_at<=180000 and owner.details.get("execution_owner") in ('p38_analysis','p39_typed'))
        workers=[]
        from engine.pd2.source_policy import DISABLED
        for role,w in sorted(beats.items()):
            if any(role==source or role.split(':')[0].endswith('_'+source) or ':'+source+':' in role for source in DISABLED):continue
            if p38_active and (role=="p37_analysis" or role.startswith("observation_analysis:") or role.startswith("analysis_owner:")):continue
            if role in ("live","jobs","notify") and now-w.updated_at>86_400_000:continue
            ttl=expected.get(role,300_000);fresh=0<=now-w.updated_at<=ttl
            workers.append({"role":role,"updated_at":w.updated_at,"state":w.state,"fresh":fresh,
                'run_scope':run_scope(w.details,run),
                "details":{k:v for k,v in (w.details or {}).items() if k in detail_keys}})
        required=('analysis_owner','anomaly_discovery','live_binance') if p38_active else ("signals","signal_projection","signal_coverage","stream_binance","candles_binance")
        overall="ok" if all(role in beats and 0<=now-beats[role].updated_at<=expected.get(role,180000) and beats[role].state not in ("error","reconnecting","partial","warming","delayed") for role in required) else "warning"
        coverage=(beats.get("signal_coverage").details or {}) if beats.get("signal_coverage") else {}
        scan=scan_view((beats.get("signals").details or {}) if beats.get("signals") else {})
        projection=beats.get("signal_projection")
        from engine.strategies.p33_reports import summary as research_summary
        research=research_summary(s)
        from engine.pd2.p35_capacity import capacity
        collection_capacity=capacity(s,now)
        data_chain=s.scalar(select(UserPreference.payload['summary']).where(UserPreference.key=='p34_data_health'))
        source_rows=[{"source":x.source,"state":x.state,"last_success_at":x.last_success_at,"message":x.message}
            for x in s.scalars(select(SourceHealth).where(SourceHealth.source.notin_(DISABLED)).order_by(SourceHealth.source))]
        counts=dict(s.execute(select(CurrentSignal.action,func.count()).group_by(CurrentSignal.action)).all())
        event_kinds=("market_update","candle_update","signal_update","source_update","settings_published","reset")
        raw=list(s.scalars(select(StreamEvent).where(StreamEvent.kind.in_(event_kinds)).order_by(StreamEvent.id.desc()).limit(60)))
        events=[]
        for event in raw:
            payload=event.payload or {};count=payload.get("count")
            if count is None and isinstance(payload.get("instruments"),list):count=len(payload["instruments"])
            events.append({"id":event.id,"kind":event.kind,"created_at":event.created_at,
                "source":payload.get("source"),"asset_id":payload.get("asset_id"),"count":count,
                "version":payload.get("version")})
    discovery=beats.get('anomaly_discovery')
    details=discovery.details if discovery else {}
    return {"as_of":now,"status":overall,"coverage":coverage,"scan":scan,
        'run_id':run,'previous_run_workers':[{'role':w.role,'run_id':w.details.get('run_id'),'state':w.state,'updated_at':w.updated_at} for w in previous_beats.values()],
        'discovery':{k:details.get(k) for k in ('snapshot_at','window_coverage','oi_window_coverage','event_delivery','scan_finished_at','scan_markets')},
        'ttls_ms':{'entry_quote':90000,'entry_book':90000,'t1_price_continuity':15000},
        "projection":{"as_of":projection.updated_at if projection else None,"state":projection.state if projection else "missing"},
        "signal_counts":counts,"workers":workers,"sources":source_rows,"events":events,"research":research,"data_chain":data_chain,"collection_capacity":collection_capacity}

@app.get("/v1/markets",response_model=MarketList)
def markets(): return list_markets()

@app.get("/v1/observations")
def observations(period:Literal["1H","4H","24H"]="1H"):
    from engine.features.observations import GROUPS, observation_groups
    data=list_markets()
    return {"as_of":data.as_of,"markets":data.model_dump(mode="json"),"kind":"observations_not_signals","period":period,
            "rule_version":"paired-direction-v1","noise_filter_pct":"0.5",
            "groups":[{"id":g,"label":label,"meaning":meaning,"tone":tone} for g,label,meaning,tone in GROUPS],
            "rows":observation_groups(data.rows,period),"strategy_status":"descriptive_observations"}

@app.get("/v1/assets")
def assets():
    with Session() as s:
        return {"as_of":now_ms(),"rows":[{"id":x.id,"symbol":x.symbol,"identity_status":x.identity_status} for x in s.scalars(select(Asset).order_by(Asset.id))]}

@app.get("/v1/instruments")
def instruments(asset: str | None=None):
    with Session() as s:
        q=select(Instrument)
        if asset:q=q.where(Instrument.asset_id==asset)
        return {"rows":[{"id":i.id,"asset_id":i.asset_id,"venue":i.venue,"native_symbol":i.native_symbol,"quote_asset":i.quote_asset,"spec":i.spec} for i in s.scalars(q)]}

@app.get("/v1/candles",response_model=CandleList)
def candles(instrument:str,resolution:Literal["5","15","30","60","120","240","480","720","1D","1W"]="240",before:int | None=None,limit:int=Query(300,ge=1,le=1500),snapshot_id:str|None=None):
    with Session() as s:
        if not s.get(Instrument,instrument):raise HTTPException(404,"市场尚未采集")
        if snapshot_id:
            from engine.pd2.service import chart_snapshot
            snap=chart_snapshot(instrument,snapshot_id=snapshot_id)
            if not snap:raise HTTPException(404,"快照不存在或不属于本市场")
            if "candles_at_snapshot" not in snap:raise HTTPException(409,"该旧快照未保存当时K线，不能用最新数据替代历史")
            stored=snap["candles_at_snapshot"].get(resolution,[])
            rows2=[b for b in stored if before is None or b["open_time"]<before][-limit:]
            return CandleList(as_of=snap["as_of"],rows=[CandleDTO(**{**r,"revision":r.get("revision",1),"instrument_id":instrument,"resolution":resolution}) for r in rows2],has_more=False,next_before=rows2[0]["open_time"] if rows2 else None,quality=Quality(status="historical_frozen"))
        if resolution in ("15","30"):
            from engine.pd2.analysis import aggregate
            from engine.pd2.service import candle_dict
            q5=select(Candle).where(Candle.instrument_id==instrument,Candle.resolution=="5")
            if before is not None:q5=q5.where(Candle.open_time<before)
            native_query=select(Candle).where(Candle.instrument_id==instrument,Candle.resolution==resolution)
            if before is not None:native_query=native_query.where(Candle.open_time<before)
            native=list(s.scalars(native_query.order_by(Candle.open_time.desc()).limit(limit+1)))
            source=[candle_dict(c) for c in reversed(list(s.scalars(q5.order_by(Candle.open_time.desc()).limit((int(resolution)//5)*(limit+2)))))]
            combined={r.open_time:{**candle_dict(r),'instrument_id':instrument,'resolution':resolution} for r in native}
            for r in aggregate(source,resolution,now_ms()):combined[r['open_time']]={**r,'instrument_id':instrument,'resolution':resolution}
            rows2=[combined[k] for k in sorted(combined)][-(limit+1):]
            more=len(rows2)>limit;rows2=rows2[-limit:]
            return CandleList(as_of=now_ms(),rows=[CandleDTO(**r) for r in rows2],has_more=more,next_before=rows2[0]['open_time'] if rows2 else None,quality=Quality(status='fresh' if rows2 else 'missing'))
        if resolution in ("120","480","720"):
            from engine.pd2.analysis import aggregate
            q2=select(Candle).where(Candle.instrument_id==instrument,Candle.resolution=="60")
            if before is not None:q2=q2.where(Candle.open_time<before)
            from engine.pd2.service import candle_dict
            source=[candle_dict(c) for c in reversed(list(s.scalars(q2.order_by(Candle.open_time.desc()).limit((int(resolution)//60)*(limit+2)))))]
            rows2=aggregate(source,resolution,now_ms())
            more=len(rows2)>limit;rows2=rows2[-limit:]
            return CandleList(as_of=now_ms(),rows=[CandleDTO(**{**r,"instrument_id":instrument}) for r in rows2],has_more=more,next_before=rows2[0]["open_time"] if rows2 else None,quality=Quality(status="fresh" if rows2 else "insufficient_history"))
        q=select(Candle).where(Candle.instrument_id==instrument,Candle.resolution==resolution)
        if before is not None:q=q.where(Candle.open_time<before)
        rows=list(s.scalars(q.order_by(Candle.open_time.desc()).limit(limit+1)))
        more=len(rows)>limit;rows=rows[:limit];rows.reverse()
        return CandleList(as_of=now_ms(),rows=[CandleDTO.model_validate(r) for r in rows],has_more=more,next_before=rows[0].open_time if rows else None,quality=Quality(status="fresh" if rows else "missing",reason=None if rows else "该周期历史尚未采集"))

@app.get("/v1/signals")
def signals(view:Literal["stages","recommended","trial","all","derivatives","buy","waiting","risk","history","opportunities"]="stages",page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=300),q:str="",sort:Literal["first","oi","space"]="first",list_snapshot:str|None=None):
    from engine.pd2.signal_projection import read
    return read(view,q,sort,page,page_size,list_snapshot)

@app.get("/v1/structure-signals/{episode_id}")
def structure_signal(episode_id:str):
    with Session() as s:
        row=s.get(Episode,episode_id)
        if not row:raise HTTPException(404,"结构事件不存在")
        campaign=s.get(Campaign,row.campaign_id)
        from engine.pd2.chart_contract import candidate_contract
        from engine.pd2.box_quality import audit
        from engine.pd2.service import candle_dict
        box=row.evidence.get('box') or {}
        quality={'status':'verified','reason':'已识别结构'} if row.evidence.get('price_pattern') else row.evidence.get('candidate_quality')
        if row.phase=='structure_pending' and box:
            source=list(s.scalars(select(Candle).where(Candle.instrument_id==campaign.reference_instrument_id,
                Candle.resolution=='240',Candle.open_time>=box.get('start_time',0),
                Candle.close_time<=box.get('end_time',0),Candle.is_closed.is_(True)).order_by(Candle.open_time).limit(61)))
            quality=audit(box,[candle_dict(c) for c in source])
        return {"id":row.id,"phase":row.phase,"phase_label":PHASES[row.phase],"evidence":row.evidence,
                "candidate_quality":quality,
                "drawing_contract":candidate_contract(row.id,campaign.reference_instrument_id,row.evidence,row.phase,row.evidence.get('evaluated_at') or now_ms(),quality),
                "first_seen_at":campaign.first_seen_at,"first_seen_price":str(campaign.first_seen_price)}

@app.get("/v1/signals/{signal_id}/overlays")
def overlays(signal_id:str):
    with Session() as s:
        snapshot=s.get(SignalSnapshot,signal_id)
        if not snapshot: raise HTTPException(404,"没有可用的信号快照")
        return {"snapshot_id":snapshot.id,"overlays":snapshot.payload.get("overlays",[])}

@app.get("/v1/sources")
def sources():
    with Session() as s:
        now=now_ms()
        rows={r.source:{"source":r.source,"state":r.state,"checked_at":r.checked_at,"last_success_at":r.last_success_at,"message":r.message,"capabilities":r.capabilities} for r in s.scalars(select(SourceHealth))}
        from engine.pd2.source_health import supplement_health
        from engine.pd2.source_policy import DISABLED,disabled_result
        for provider in DISABLED:
            rows[provider]={**disabled_result(provider),'source':provider,'message':'已停用外部实时来源；仅保留历史'}
        return {"as_of":now,"rows":[rows[k] for k in sorted(rows)]}

@app.get("/v1/settings/schema")
def settings_schema():return RuntimeConfig.model_json_schema()

@app.get("/v1/settings/active")
def get_settings():
    with Session() as s:
        r=active_config(s);return {"version":r.id,"config":RuntimeConfig.model_validate(r.config).model_dump(),"published_at":r.published_at,"scope":"本机单管理员"}

@app.post("/v1/settings/drafts")
def draft(body:DraftRequest):
    with Session.begin() as s:
        active=active_config(s)
        r=SettingsVersion(state="draft",config=body.config.model_dump(),created_at=now_ms(),parent_version=active.id)
        s.add(r);s.flush();return {"draft_id":r.id,"expected_version":active.id}

@app.post("/v1/settings/drafts/{draft_id}/publish")
def publish(draft_id:int,body:PublishRequest):
    with Session.begin() as s:
        s.execute(text("SELECT pg_advisory_xact_lock(1877102)"))
        active=active_config(s)
        if active.id!=body.expected_version:raise HTTPException(409,"设置已被更新，请重新加载")
        draft=s.get(SettingsVersion,draft_id)
        if not draft or draft.state!="draft":raise HTTPException(404,"草稿不存在")
        draft.config=RuntimeConfig.model_validate(draft.config).model_dump()
        draft.state="published";draft.published_at=now_ms()
        emit(s,"settings_published",{"version":draft.id})
        return {"version":draft.id,"effective":"下一采集轮次"}

@app.get("/v1/settings/versions")
def versions():
    with Session() as s:
        return {"rows":[{"version":r.id,"state":r.state,"created_at":r.created_at,"config":r.config} for r in s.scalars(select(SettingsVersion).order_by(SettingsVersion.id.desc()).limit(30))]}

@app.get("/v1/watchlist")
def watchlist():
    with Session() as s:return {"asset_ids":[r.asset_id for r in s.scalars(select(Watchlist))]}

@app.post("/v1/watchlist/{asset_id}")
def watch(asset_id:str):
    with Session.begin() as s:
        if not s.get(Asset,asset_id):raise HTTPException(404,"资产不存在")
        if not s.get(Watchlist,asset_id):s.add(Watchlist(asset_id=asset_id,created_at=now_ms()))
    return {"asset_id":asset_id,"saved":True}

@app.delete("/v1/watchlist/{asset_id}")
def unwatch(asset_id:str):
    with Session.begin() as s:
        row=s.get(Watchlist,asset_id)
        if row:s.delete(row)
    return {"asset_id":asset_id,"saved":False}

@app.get("/v1/notifications")
def notifications():
    from engine.repository import runtime_config
    with Session() as s:
        return {"enabled":runtime_config().notifications.enabled,"targets":[{"id":r.id,"enabled":r.enabled,"chat_id":r.chat_id} for r in s.scalars(select(NotificationTarget))],"rows":[{"id":r.id,"state":r.state,"expires_at":r.expires_at} for r in s.scalars(select(Outbox).order_by(Outbox.expires_at.desc()).limit(100))],"message":"Token仅后端读取；未授权目标不会发送"}

@app.get("/v1/history")
def history():
    with Session() as s:
        return {"rows":[{"id":r.id,"as_of":r.as_of,"plan_id":r.plan_id,"asset_id":r.payload.get("asset_id"),"instrument_id":r.payload.get("instrument_id"),"episode_id":r.payload.get("episode_id"),"action":r.payload.get("action"),"headline":r.payload.get("trade_thesis",{}).get("headline")} for r in s.scalars(select(SignalSnapshot).order_by(SignalSnapshot.as_of.desc()).limit(200))],"message":"不可变决策快照；不代表真实下单"}

@app.get("/v1/stream")
async def stream(request:Request):
    try:after=max(0,int(request.headers.get("last-event-id","0")))
    except ValueError:after=0
    async def events():
        nonlocal after
        queue=asyncio.Queue(maxsize=64);hub.queues.add(queue)
        try:
            yield ": connected\n\n"
            replay=await asyncio.to_thread(read_events,after,201)
            if len(replay)>200:
                after=hub.cursor
                yield f"id: {after}\nevent: reset\ndata: {{}}\n\n"
            else:
                for item in replay:
                    after=item["id"]
                    yield f"id: {after}\nevent: {item['kind']}\ndata: {json.dumps(item['payload'])}\n\n"
            while not await request.is_disconnected():
                try:item=await asyncio.wait_for(queue.get(),timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n";continue
                if item["id"]<=after:continue
                after=item["id"]
                yield f"id: {after}\nevent: {item['kind']}\ndata: {json.dumps(item['payload'])}\n\n"
        finally:hub.queues.discard(queue)
    return StreamingResponse(events(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

# PD2: one backend decision contract feeds chart, list and notifications.
@app.get("/v1/research/events")
def research_events(instrument:str|None=None,limit:int=Query(30,ge=1,le=100)):
    from engine.strategies.p33_reports import events
    with Session() as s:return events(s,instrument,limit)

@app.get("/v1/analysis")
def analysis(instrument:str,snapshot_id:str|None=None):
    from engine.pd2.service import analyze_market
    if snapshot_id:
        from engine.pd2.service import chart_snapshot
        snap=chart_snapshot(instrument,snapshot_id=snapshot_id)
        if not snap:raise HTTPException(404,"历史快照不存在或不属于本市场")
        return {"instrument_id":instrument,"as_of":snap["as_of"],"timeframes":snap["timeframe_assessments"],"opportunity":snap["opportunity_plan"],"price":snap["market"]["price"],"algorithm_version":snap["rule_version"],"validation_status":"unvalidated","probability":None}
    result=analyze_market(instrument)
    if result is None:raise HTTPException(404,"市场不存在")
    return result

@app.get("/v1/analysis/series")
def analysis_series(instrument:str,resolution:Literal['60','240','1D','1W'],before:int|None=None,
                    limit:int=Query(500,ge=1,le=1500),snapshot_id:str|None=None):
    from engine.pd2.indicator_history import page
    from engine.pd2.service import chart_snapshot
    snap=chart_snapshot(instrument,snapshot_id=snapshot_id) if snapshot_id else None
    if snapshot_id and not snap:raise HTTPException(404,'快照不存在或市场不一致')
    with Session() as session:return page(session,instrument,resolution,before,limit,now_ms(),snap)

@app.get("/v1/derivatives/history")
def derivatives_history(instrument:str,before:int|None=None,limit:int=Query(500,ge=1,le=2000),snapshot_id:str|None=None):
    from engine.pd2.service import referenced_derivative_history,chart_snapshot
    if snapshot_id:
        snap=chart_snapshot(instrument,snapshot_id=snapshot_id)
        if not snap:raise HTTPException(404,"历史快照不存在或不属于本市场")
        if "derivative_history_at_snapshot" not in snap:raise HTTPException(409,"该旧快照未保存当时合约序列，不能用当前序列替代")
        return snap["derivative_history_at_snapshot"]
    result=referenced_derivative_history(instrument,before,limit)
    if result is None:raise HTTPException(404,"市场不存在")
    return result

@app.get("/v1/chart/snapshot")
def get_chart_snapshot(instrument:str,episode:str|None=None,snapshot_id:str|None=None):
    from engine.pd2.service import chart_snapshot
    result=chart_snapshot(instrument,episode,snapshot_id)
    if snapshot_id and not result:raise HTTPException(404,"快照不存在或不属于当前市场")
    return {"snapshot":result,"historical":snapshot_id is not None}

@app.get("/v1/signals/{episode_id}/snapshots")
def snapshots(episode_id:str,limit:int=Query(100,ge=1,le=500),snapshot_id:str|None=None):
    with Session() as s:
        query=select(SignalSnapshot).join(Plan).where(Plan.episode_id==episode_id)
        if snapshot_id:
            frozen=s.get(SignalSnapshot,snapshot_id)
            if not frozen or frozen.payload.get("episode_id")!=episode_id:
                raise HTTPException(404,"快照不属于当前事件")
            query=query.where(SignalSnapshot.as_of<=frozen.as_of,SignalSnapshot.plan_id==frozen.plan_id)
        rows=list(s.scalars(query.order_by(SignalSnapshot.as_of.desc(),SignalSnapshot.id.desc()).limit(limit+1)))
        return {"truncated":len(rows)>limit,"rows":[{"id":r.id,"as_of":r.as_of,"action":r.payload["action"],"plan_id":r.plan_id,
            "phase":r.payload.get("phase"),"phase_label":r.payload.get("phase_label"),
            "price":(r.payload.get("market") or {}).get("price"),
            "protection":r.payload.get("current_protection"),"tracking":r.payload.get("tracking") or {}}
            for r in rows[:limit]]}

@app.get("/v1/coverage")
def coverage(subject:str|None=None,historical:bool=False):
    from engine.pd2.source_policy import REALTIME,VERSION
    with Session() as s:
        q=select(DataCapability)
        if not historical:q=q.where(DataCapability.provider.in_(REALTIME))
        if subject:q=q.where(DataCapability.subject==subject)
        now=now_ms()
        rows=[{"provider":r.provider,"capability":r.capability,"subject":r.subject,
          "status":"stale" if r.status=="fresh" and r.expires_at<now else r.status,
          "observed_at":r.observed_at,"received_at":r.received_at,"next_refresh_at":r.next_refresh_at,"reason":r.reason}
          for r in s.scalars(q.order_by(DataCapability.provider,DataCapability.subject))]
    return {"rows":rows,"budgets":[],"historical":historical,"source_policy":VERSION}

@app.get("/v1/supplement/{provider}/{capability}")
def supplement(provider:Literal["cmc","bykaranteli"],capability:str,subject:str="global"):
    from engine.pd2.source_policy import disabled_result
    return disabled_result(provider)

@app.post("/v1/settings/validate")
def validate_settings(body:DraftRequest):
    cfg=body.config
    return {"valid":True,"config":cfg.model_dump(),"month_plan":{},
        "source_policy":"binance_only","warnings":["币安请求使用统一权重调度；历史计划保留冻结配置"]}

@app.post("/v1/settings/rollback/{version}")
def rollback_settings(version:int,body:PublishRequest):
    with Session.begin() as s:
        s.execute(text("SELECT pg_advisory_xact_lock(1877102)"))
        current=active_config(s)
        if current.id!=body.expected_version:raise HTTPException(409,"当前版本已改变")
        previous=s.get(SettingsVersion,version)
        if not previous or previous.state!="published":raise HTTPException(404,"历史发布版本不存在")
        cfg=RuntimeConfig.model_validate(previous.config)
        row=SettingsVersion(state="published",config=cfg.model_dump(),created_at=now_ms(),published_at=now_ms(),parent_version=current.id)
        s.add(row);s.flush();emit(s,"settings_published",{"version":row.id,"restored_from":version})
        return {"version":row.id,"restored_from":version}

class PreferenceRequest(BaseModel):
    payload:dict=Field(default_factory=dict)
@app.get("/v1/preferences/{key}")
def preference(key:str):
    with Session() as s:
        row=s.get(UserPreference,key)
        return {"payload":row.payload if row else {}}
@app.put("/v1/preferences/{key}")
def save_preference(key:str,body:PreferenceRequest):
    if len(key)>120 or len(json.dumps(body.payload))>20000:raise HTTPException(422,"偏好记录过大")
    with Session.begin() as s:s.merge(UserPreference(key=key,payload=body.payload,updated_at=now_ms()))
    return {"saved":True}

class BindTargetRequest(BaseModel):
    chat_id:str=Field(min_length=1,max_length=80,pattern=r"^-?[0-9]+$")
    authorized:bool=False
@app.post("/v1/notifications/targets")
def bind_target(body:BindTargetRequest):
    if not body.authorized:raise HTTPException(422,"必须明确授权该聊天接收提醒")
    from hashlib import sha256
    ident=sha256(body.chat_id.encode()).hexdigest()[:24]
    with Session.begin() as s:s.merge(NotificationTarget(id=ident,chat_id=body.chat_id,enabled=True,
      authorized_at=now_ms(),subscriptions={"actions":["participate","trial","ended"]}))
    return {"id":ident,"enabled":True,"message":"目标已绑定；全局开关与后端Token就绪后才发送"}

@app.delete("/v1/notifications/targets/{target_id}")
def disable_target(target_id:str):
    with Session.begin() as s:
        row=s.get(NotificationTarget,target_id)
        if not row:raise HTTPException(404,"目标不存在")
        row.enabled=False
    return {"enabled":False}

@app.get("/v1/validation")
def validation_report():
    with Session() as s:
        counts=dict(s.execute(select(PlanOutcome.state,func.count()).group_by(PlanOutcome.state)).all())
        return {"validation_status":"insufficient_samples","probability":None,"outcomes":counts,
          "message":"前向价格路径记录不等于成交回测；未校准不发布上涨概率"}

@app.get("/v1/implementation")
def implementation():
    from pathlib import Path
    path=Path(__file__).resolve().parents[2]/"docs"/"pd2-acceptance.json"
    return json.loads(path.read_text(encoding="utf8")) if path.exists() else {"status":"in_progress"}


class LoginRequest(BaseModel):
    token:str=Field(min_length=1,max_length=500)
@app.post("/v1/auth/login")
def login(body:LoginRequest,request:Request):
    import hmac
    from engine.pd2.auth import secret,create_session,login_allowed
    if not settings().require_auth:raise HTTPException(400,"当前为本机模式，无需登录")
    if not login_allowed(request.client.host if request.client else "unknown"):raise HTTPException(429,"尝试次数过多，请5分钟后重试")
    try:key=secret()
    except (OSError,ValueError):raise HTTPException(503,"管理员认证未配置")
    if not hmac.compare_digest(body.token,key):raise HTTPException(401,"登录信息不正确")
    cookie,data=create_session(key)
    response=JSONResponse({"authenticated":True,"expires":data["expires"]})
    secure=settings().web_origin.startswith("https://")
    response.set_cookie("erji_session",cookie,httponly=True,secure=secure,samesite="strict",max_age=settings().session_seconds,path=settings().cookie_path)
    response.set_cookie("erji_csrf",data["csrf"],httponly=False,secure=secure,samesite="strict",max_age=settings().session_seconds,path=settings().cookie_path)
    return response
@app.post("/v1/auth/logout")
def logout():
    response=JSONResponse({"authenticated":False})
    response.delete_cookie("erji_session",path=settings().cookie_path);response.delete_cookie("erji_csrf",path=settings().cookie_path)
    return response


@app.get('/v1/analysis/registry')
def analysis_registry():
    from pathlib import Path
    return json.loads((Path(__file__).resolve().parents[2]/'docs'/'analysis-registry.json').read_text(encoding='utf8'))


class ResearchPosition(BaseModel):
    id:str=Field(min_length=1,max_length=80)
    instrument_id:str
    entry:Decimal=Field(gt=0)
    quantity:Decimal=Field(gt=0)
    stop:Decimal|None=Field(default=None,gt=0)
    state:Literal['open','closed']='open'
    kind:Literal['paper','user_declared']='paper'
class PortfolioRequest(BaseModel):
    account_usd:Decimal=Field(ge=0)
    per_plan_pct:Decimal=Field(gt=0,le=100)
    total_pct:Decimal=Field(gt=0,le=100)
    positions:list[ResearchPosition]=Field(default_factory=list,max_length=100)
@app.get('/v1/portfolio')
def get_portfolio():
    from engine.pd2.portfolio import portfolio
    return portfolio()
@app.put('/v1/portfolio')
def put_portfolio(body:PortfolioRequest):
    if body.per_plan_pct>body.total_pct:raise HTTPException(422,'单计划风险不能大于总风险额度')
    if len({r.id for r in body.positions})!=len(body.positions):raise HTTPException(422,'仓位记录ID不能重复')
    with Session.begin() as s:
        for p in body.positions:
            if not s.get(Instrument,p.instrument_id):raise HTTPException(422,'市场尚未登记')
        s.merge(UserPreference(key='research_portfolio',payload=body.model_dump(mode='json'),updated_at=now_ms()))
    return {'saved':True,'kind':'research_only','orders_submitted':0}


@app.get('/v1/strategy-templates')
def strategy_templates():
    from engine.strategies.p35_templates import catalog
    return {'rows':catalog()}

@app.get('/v1/strategy-templates/{rule}/document')
def strategy_template_document(rule:str):
    from fastapi.responses import HTMLResponse
    from engine.strategies.p35_templates import document
    content=document(rule)
    if content is None:raise HTTPException(404,'模板不存在')
    return HTMLResponse(content)

@app.get('/v1/strategy-cases/{case_id}')
def strategy_case(case_id:str):
    import json
    from engine.strategies.p33_cases import CATALOG
    case=next((r for r in json.loads(CATALOG.read_text(encoding='utf-8'))['cases'] if r['id']==case_id),None)
    if not case:raise HTTPException(404,'案例不存在')
    return case


@app.get('/v1/chart/structures')
def chart_structures(instrument:str,as_of:int|None=None):
    from engine.pd2.chart_contract import drawing_contract
    cutoff=min(as_of or now_ms(),now_ms())
    with Session() as s:
        records=s.scalars(select(SignalSnapshot).join(Plan).where(Plan.execution_instrument_id==instrument,SignalSnapshot.as_of<=cutoff).distinct(SignalSnapshot.plan_id).order_by(SignalSnapshot.plan_id,SignalSnapshot.as_of.desc()))
        snapshots=sorted(records,key=lambda r:r.as_of,reverse=True)[:4]
        return {'rows':[drawing_contract(r.payload) for r in snapshots]}


from services.api.observations import router as observations_router
app.include_router(observations_router)

@app.get('/v1/settings/effective')
def effective_configuration():
    from engine.p38.configuration import effective
    return effective()

@app.get('/v1/diagnostics/pipeline')
def pipeline_diagnostics():
    from engine.p38.diagnostics import stages
    return stages(hub.status())

@app.get('/v1/diagnostics/causal-trace')
def causal_trace(instrument:str,limit:int=Query(20,ge=1,le=100)):
    from engine.p39.diagnostics import trace
    return trace(instrument,limit)
