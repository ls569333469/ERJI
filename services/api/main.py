import asyncio, json
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
        self.queues=set();self.cursor=0
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
            except Exception:
                pass  # /health reports dependency failure; no credentials in logs
            await asyncio.sleep(1)

hub=EventHub()
@asynccontextmanager
async def lifespan(app):
    if settings().require_auth:
        from engine.pd2.auth import secret
        secret()
    await asyncio.to_thread(bootstrap)
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
    with Session() as s:
        s.execute(text("SELECT 1"))
        workers=[{"role":w.role,"updated_at":w.updated_at,"state":w.state,"fresh":now_ms()-w.updated_at<300_000} for w in s.scalars(select(WorkerHeartbeat))]
    return {"status":"ok","database":"postgresql","mode":settings().mode,"schema_version":SCHEMA_VERSION,"workers":workers,"strategy_status":"pd2_decisions","execution_status":"native_book_cost_gated","telegram_status":"not_enabled"}

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
def candles(instrument:str,resolution:Literal["15","30","60","120","240","480","720","1D","1W"]="240",before:int | None=None,limit:int=Query(300,ge=1,le=1500),snapshot_id:str|None=None):
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
def signals(view:Literal["stages","recommended","trial","all","derivatives","buy","waiting","risk","history"]="stages",page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100),q:str="",sort:Literal["first","oi","space"]="first",list_snapshot:str|None=None):
    from engine.pd2.signal_projection import read
    return read(view,q,sort,page,page_size,list_snapshot)

@app.get("/v1/structure-signals/{episode_id}")
def structure_signal(episode_id:str):
    with Session() as s:
        row=s.get(Episode,episode_id)
        if not row:raise HTTPException(404,"结构事件不存在")
        campaign=s.get(Campaign,row.campaign_id)
        return {"id":row.id,"phase":row.phase,"phase_label":PHASES[row.phase],"evidence":row.evidence,
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
        for provider in ('cmc','bykaranteli'):
            records=list(s.scalars(select(DataCapability).where(DataCapability.provider==provider)))
            rows[provider]=supplement_health(provider,records,now)
        return {"as_of":now,"rows":[rows[k] for k in sorted(rows)]}

@app.get("/v1/settings/schema")
def settings_schema():return RuntimeConfig.model_json_schema()

@app.get("/v1/settings/active")
def get_settings():
    with Session() as s:
        r=active_config(s);return {"version":r.id,"config":r.config,"published_at":r.published_at,"scope":"本机单管理员"}

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

@app.get("/v1/derivatives/history")
def derivatives_history(instrument:str,before:int|None=None,limit:int=Query(500,ge=1,le=2000),snapshot_id:str|None=None):
    from engine.pd2.service import derivative_history,chart_snapshot
    if snapshot_id:
        snap=chart_snapshot(instrument,snapshot_id=snapshot_id)
        if not snap:raise HTTPException(404,"历史快照不存在或不属于本市场")
        if "derivative_history_at_snapshot" not in snap:raise HTTPException(409,"该旧快照未保存当时合约序列，不能用当前序列替代")
        return snap["derivative_history_at_snapshot"]
    result=derivative_history(instrument,before,limit)
    if result is None:raise HTTPException(404,"市场不存在")
    return result

@app.get("/v1/chart/snapshot")
def get_chart_snapshot(instrument:str,episode:str|None=None,snapshot_id:str|None=None):
    from engine.pd2.service import chart_snapshot
    result=chart_snapshot(instrument,episode,snapshot_id)
    if snapshot_id and not result:raise HTTPException(404,"快照不存在或不属于当前市场")
    return {"snapshot":result,"historical":snapshot_id is not None}

@app.get("/v1/signals/{episode_id}/snapshots")
def snapshots(episode_id:str,limit:int=Query(100,ge=1,le=500)):
    with Session() as s:
        rows=list(s.scalars(select(SignalSnapshot).join(Plan).where(Plan.episode_id==episode_id).order_by(SignalSnapshot.as_of.desc()).limit(limit)))
        return {"rows":[{"id":r.id,"as_of":r.as_of,"action":r.payload["action"],"plan_id":r.plan_id} for r in rows]}

@app.get("/v1/coverage")
def coverage(subject:str|None=None):
    from engine.pd2.storage import budget_status
    from engine.repository import runtime_config
    with Session() as s:
        q=select(DataCapability)
        if subject:q=q.where(DataCapability.subject==subject)
        now=now_ms()
        rows=[{"provider":r.provider,"capability":r.capability,"subject":r.subject,
          "status":"stale" if r.status=="fresh" and r.expires_at<now else r.status,
          "observed_at":r.observed_at,"received_at":r.received_at,"next_refresh_at":r.next_refresh_at,"reason":r.reason}
          for r in s.scalars(q.order_by(DataCapability.provider,DataCapability.subject))]
    return {"rows":rows,"budgets":budget_status(runtime_config())}

@app.get("/v1/supplement/{provider}/{capability}")
def supplement(provider:Literal["cmc","bykaranteli"],capability:str,subject:str="global"):
    from engine.pd2.storage import get
    result=get(provider,capability,subject)
    if result.get("payload") is not None and provider=="bykaranteli":result["attribution"]="ByKaranteli"
    return result

@app.post("/v1/settings/validate")
def validate_settings(body:DraftRequest):
    cfg=body.config
    from engine.pd2.storage import budget_plan
    with Session() as s:
        catalog=s.get(DataCapability,"bykaranteli:symbols:global")
        map_count=len((catalog.payload if catalog else {}).get("symbols",[]))
    plan=budget_plan(cfg,map_count)
    estimate=plan["bykaranteli"]["baseline_month_requests"]
    return {"valid":True,"config":cfg.model_dump(),"month_plan":plan,"budget_estimate":{"bykaranteli_30d":estimate,
        "assumed_map_count":map_count,"kind":"planning_estimate_not_actual_usage","available":int(cfg.sources.bykaranteli.month_budget*(1-cfg.sources.bykaranteli.reserve_pct/100))-cfg.sources.bykaranteli.external_used},
        "warnings":["地图目录变化后须重新核算；持仓保护任务优先","显示参数不改变已经冻结的Plan"]}

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
