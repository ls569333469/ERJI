"""Transactional notification outbox. Targets require explicit binding authorization."""
import json,ssl
from urllib.parse import quote,urlparse,urlencode
from hashlib import sha256
import httpx
from sqlalchemy import select
from engine.db import Session
from engine.models import NotificationTarget,Outbox,SignalSnapshot,Plan
from engine.adapters.base import now_ms
from engine.config import settings
from engine.pd2.providers import read_key

def render(snapshot,public_url=""):
    p=snapshot;op=p.get("opportunity_plan",{});t=op.get("primary") or op.get("t1");w=op.get("highest_conditional_target")
    lines=[p["asset_id"]+" · "+p["action"]["label"],p.get("trade_thesis",{}).get("headline",""),
      "参与区 "+ "–".join(p["evidence"].get("entry_zone",[])),"保护价 "+str(p.get("current_protection")),
      "首次信号 "+str(p.get("first_seen_price"))]
    if t:lines.append("主目标 "+t["low"]+"–"+t["high"]+"（"+t["method"]+"）")
    if w:lines.append("最高条件目标 "+w["low"]+"；"+op.get("highest_condition","按原计划条件"))
    lines.append("依据："+p["action"].get("reason",""))
    if t and t.get("remaining_upside_pct") is not None:lines.append("距主目标 "+t["remaining_upside_pct"]+"%；价格倍数 "+str(t.get("price_multiple")))
    if w:lines.extend("延伸条件："+str(g.get("timeframe"))+" "+str(g.get("condition") or ("收盘高于 "+str(g.get("price")))) for g in w.get("dependencies",[]))
    thesis=p.get("trade_thesis",{})
    if thesis.get("opposition"):lines.append("反证："+"；".join(thesis["opposition"]))
    if thesis.get("missing"):lines.append("缺口："+"；".join(thesis["missing"]))
    lines.append("历史验证不足，未发布上涨概率。")
    if public_url.startswith("https://") and urlparse(public_url).hostname not in ("localhost","127.0.0.1","::1"):lines.append(public_url.rstrip("/")+"/assets/"+quote(p["asset_id"],safe="")+"?"+urlencode({"instrument":p["instrument_id"],"episode":p["episode_id"],"snapshot":p["id"]}))
    return "\n".join(filter(None,lines))[:4000]

def enqueue(s,event_id,snapshot,config):
    if snapshot["action"]["code"] not in ("participate","trial","ended"):return
    for target in s.scalars(select(NotificationTarget).where(NotificationTarget.enabled==True)):
        if not target.authorized_at:continue
        codes=target.subscriptions.get("actions",["participate","trial","ended"])
        if snapshot["action"]["code"] not in codes:continue
        key=sha256(f'{target.id}:{snapshot["plan_id"]}:{snapshot["action"]["code"]}:{snapshot["rule_version"]}'.encode()).hexdigest()
        if s.scalar(select(Outbox.id).where(Outbox.dedupe_key==key)):continue
        oid=sha256(key.encode()).hexdigest()
        scheduled=now_ms()
        if snapshot["action"]["code"]!="ended":
            for prior in s.scalars(select(Outbox).where(Outbox.expires_at>scheduled-config.cooldown_seconds*1000)):
                if prior.payload.get("target_id")==target.id and prior.payload.get("asset_id")==snapshot["asset_id"] and prior.payload.get("action")==snapshot["action"]["code"] and prior.state not in ("expired","failed"):
                    scheduled=max(scheduled,prior.payload.get("created_at",0)+config.cooldown_seconds*1000)
        s.add(Outbox(id=oid,event_id=event_id,dedupe_key=key,state="pending",
           expires_at=now_ms()+config.ttl_seconds*1000,next_attempt_at=scheduled,lease_until=None,
           payload={"target_id":target.id,"snapshot_id":snapshot["id"],"asset_id":snapshot["asset_id"],"action":snapshot["action"]["code"],"created_at":now_ms(),"text":render(snapshot,config.public_app_url)}))

async def dispatch_once():
    try:token=read_key(settings().telegram_token_file)
    except (OSError,ValueError):token=None
    if not token:return {"status":"missing_key","sent":0}
    now=now_ms()
    with Session.begin() as s:
        # A crash after send starts has unknown delivery, never silently retried.
        for old in s.scalars(select(Outbox).where(Outbox.state=="sending",Outbox.lease_until<now)):
            old.state="delivery_unknown"
        rows=list(s.scalars(select(Outbox).where(Outbox.state.in_(["pending","retry"]),Outbox.next_attempt_at<=now).with_for_update(skip_locked=True).limit(10)))
        selected=[]
        for row in rows:
            target=s.get(NotificationTarget,row.payload["target_id"])
            snap=s.get(SignalSnapshot,row.payload["snapshot_id"])
            latest=s.scalar(select(SignalSnapshot).where(SignalSnapshot.plan_id==snap.plan_id).order_by(SignalSnapshot.as_of.desc()).limit(1)) if snap else None
            if row.expires_at<=now or not target or not target.enabled or not target.authorized_at or not latest or latest.payload["action"]["code"]!=snap.payload["action"]["code"] or now-latest.as_of>90000:
                row.state="expired";continue
            if latest.payload["action"]["code"] in ("participate","trial") and not 0<=now-(latest.payload.get("execution") or {}).get("received_at",0)<=10000:
                row.state="expired";continue
            from engine.repository import runtime_config
            message=render(latest.payload,runtime_config().notifications.public_app_url)
            row.payload={**row.payload,"snapshot_id":latest.id,"text":message}
            row.state="sending";row.lease_until=now+60000
            selected.append((row.id,target.chat_id,message))
    sent=0
    async with httpx.AsyncClient(timeout=15,verify=ssl.create_default_context()) as client:
        for oid,chat,message in selected:
            state,extra=await send_text(client,token,chat,message)
            if state=="sent":sent+=1
            with Session.begin() as s:
                row=s.get(Outbox,oid);row.state=state;row.payload={**row.payload,**extra}
                if state=="retry":row.next_attempt_at=now_ms()+extra["retry_after"]*1000
    return {"status":"ok","sent":sent}

async def send_text(client,token,chat,message):
    """Only explicit Telegram rejection is retriable; unknown delivery is not."""
    try:
        response=await client.post("https://api.telegram.org/bot"+token+"/sendMessage",json={"chat_id":chat,"text":message,"link_preview_options":{"is_disabled":True}})
        body=response.json()
        if response.status_code==429:return "retry",{"retry_after":max(1,int(body.get("parameters",{}).get("retry_after",60)))}
        if body.get("ok") and body.get("result",{}).get("message_id") is not None:return "sent",{"message_id":body["result"]["message_id"]}
        if body.get("ok") is False and response.status_code>=500:return "retry",{"retry_after":60,"error_code":body.get("error_code")}
        if response.status_code<500:return "failed",{"error_code":body.get("error_code")}
        return "delivery_unknown",{"reason":"ambiguous_server_response"}
    except Exception as error:return "delivery_unknown",{"reason":type(error).__name__}
