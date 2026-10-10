"""Shared capability cache and provider budgets, with persistent state and provenance."""
from datetime import datetime,timezone
import calendar
from hashlib import sha256
import json
from sqlalchemy import select,text
from engine.db import Session
from engine.models import DataCapability,ProviderBudget,RawEvent,UserPreference
from engine.adapters.base import now_ms

def month_key(now=None):
    return datetime.fromtimestamp((now or now_ms())/1000,timezone.utc).strftime("%Y-%m")
def reserve(provider,config,cost=1,now=None,priority="normal"):
    now=now or now_ms();month=month_key(now)
    with Session.begin() as s:
        if s.bind.dialect.name=="postgresql":s.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),{"key":"budget:"+provider})
        row=s.get(ProviderBudget,(provider,month))
        if row is None:row=ProviderBudget(provider=provider,month=month,used=0,blocked_until=0);s.add(row)
        # At least ten percent remains untouched, including critical requests.
        cap=int(config.month_budget*(min(.90,1-config.reserve_pct/100) if priority=="critical" else min(.85,1-config.reserve_pct/100)))
        account=s.get(UserPreference,'account_usage:'+provider)
        used=row.used+config.external_used
        if account and account.payload.get('month')==month:
            report=account.payload
            used=max(used,report['credits_used']+max(0,row.used-report['local_baseline']))
            cap=min(cap,int(report['monthly_limit']*(1-config.reserve_pct/100)))
        usage=used/config.month_budget
        if usage>=.95:return False,"budget_exhausted"
        if usage>=.85 and priority!="critical":return False,"budget_priority_only"
        if usage>=.70 and priority=="background":return False,"budget_reduced"
        key="request_rate:"+provider
        ledger=s.get(UserPreference,key)
        times=[t for t in (ledger.payload.get("times",[]) if ledger else []) if now-60000<t<=now]
        if len(times)>=28:return False,"rate_limited"
        if row.blocked_until>now:return False,"rate_limited"
        if used+cost>cap:return False,"budget_exhausted"
        row.used+=cost
        s.merge(UserPreference(key=key,payload={"times":times+[now]},updated_at=now))
    return True,None


def account_usage(provider,document,received):
    data=document.get('data') or {};usage=(data.get('usage') or {}).get('current_month') or {}
    used=usage.get('credits_used');limit=(data.get('plan') or {}).get('credit_limit_monthly')
    if used is None or limit is None:return
    with Session.begin() as s:
        key='account_usage:'+provider;old=s.get(UserPreference,key)
        if old and old.updated_at>=received:return
        local=s.get(ProviderBudget,(provider,month_key()))
        s.merge(UserPreference(key=key,updated_at=received,payload={'month':month_key(),'credits_used':int(used),
            'monthly_limit':int(limit),'credits_left':usage.get('credits_left'),'local_baseline':local.used if local else 0}))
def reconcile(provider,extra=0,retry_after=0):
    with Session.begin() as s:
        row=s.scalar(select(ProviderBudget).where(ProviderBudget.provider==provider,ProviderBudget.month==month_key()).with_for_update())
        if row:
            row.used+=max(0,extra)
            row.blocked_until=max(row.blocked_until,now_ms()+int(retry_after*1000))

def reference_turn(provider,daily_remaining,now=None):
    """Slow references share at most 45% of remaining daily credits.

    Quotes, FX and account/identity checks retain the other share. This is a
    collection allocation, never a longer live evidence validity window.
    """
    now=now or now_ms();allowance=int(daily_remaining*.45)
    interval=max(60000,(86400000+max(1,allowance)-1)//max(1,allowance))
    with Session.begin() as s:
        key='reference_pacing:'+provider
        s.execute(text('SELECT pg_advisory_xact_lock(hashtext(current_schema() || :key))'),{'key':key})
        row=s.get(UserPreference,key);old=row.payload if row else {}
        due=old.get('next_at',0)
        if not allowance:return False,now+3600000
        if now<due:return False,due
        due=now+interval
        s.merge(UserPreference(key=key,updated_at=now,payload={'next_at':due,'interval_ms':interval,
            'daily_credit_allowance':allowance,'usage':'background_references_only'}))
    return True,due

def reconcile_reference_cost(provider,extra):
    if extra<=0:return
    with Session.begin() as s:
        key='reference_pacing:'+provider
        s.execute(text('SELECT pg_advisory_xact_lock(hashtext(current_schema() || :key))'),{'key':key})
        row=s.get(UserPreference,key)
        if row:row.payload={**row.payload,'next_at':row.payload['next_at']+extra*row.payload['interval_ms']}
def put(provider,capability,subject,payload,observed_at,ttl_ms,status="fresh",reason=None,endpoint="",next_ms=None):
    now=now_ms();key=f"{provider}:{capability}:{subject}"
    raw_id=sha256((key+str(now)+json.dumps(payload,sort_keys=True,separators=(",",":"))).encode()).hexdigest()
    with Session.begin() as s:
        from sqlalchemy.dialects.postgresql import insert
        s.execute(insert(RawEvent).values(id=raw_id,source=provider,endpoint=endpoint[:150],received_at=now,payload=payload).on_conflict_do_nothing(index_elements=['id']))
        s.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':'capability:'+key})
        old=s.get(DataCapability,key,with_for_update=True)
        if old and status=='fresh' and ((observed_at is not None and old.observed_at is not None and observed_at<old.observed_at) or now<old.received_at):return old.raw_ref
        value_status = status if status != "fresh" or observed_at is not None else "source_time_unknown"
        if capability=='execution_book' and payload.get('source_time_kind')=='snapshot_received':
            from engine.pd2.book_clock import fresh
            value_status='fresh' if status=='fresh' and fresh(payload,now) else 'snapshot_latency_invalid'
        if status == "fresh" and observed_at is not None and observed_at > now:
            value_status = "clock_invalid"
        # Transport success can have an unknown market timestamp. Keep that
        # response with its own provenance; it is never action-fresh.
        accepted = status == "fresh"
        if old and not accepted and old.payload:
            payload, observed_at = old.payload, old.observed_at
            raw_ref, received_at, expires_at = old.raw_ref, old.received_at, old.expires_at
            last_success_at, last_success_raw_ref = old.last_success_at, old.last_success_raw_ref
        else:
            raw_ref, received_at = raw_id, now
            expires_at = (observed_at if observed_at is not None else now) + ttl_ms
            last_success_at = now if accepted else None
            last_success_raw_ref = raw_id if accepted else None
        s.merge(DataCapability(key=key,provider=provider,capability=capability,subject=subject,
            status=value_status,observed_at=observed_at,received_at=received_at,expires_at=expires_at,
            next_refresh_at=next_ms,reason=reason,raw_ref=raw_ref,
            last_success_at=last_success_at,last_success_raw_ref=last_success_raw_ref,
            last_attempt_at=now,last_attempt_raw_ref=raw_id,payload=payload))
    return raw_id
def get(provider,capability,subject,now=None):
    now=now or now_ms()
    with Session() as s:
        row=s.get(DataCapability,f"{provider}:{capability}:{subject}")
        if not row:return {"status":"not_collected","reason":"尚无采集记录","payload":None,"observed_at":None}
        status=row.status
        if row.observed_at and row.observed_at>now:status="clock_invalid"
        if status=="fresh" and row.expires_at<now:status="stale"
        from engine.pd2.capability_policy import describe
        policy=describe(provider,capability,row.observed_at,now)
        if policy and status not in ('fresh','stale'):
            policy['reference_status']=status
        return {"status":status,"reason":row.reason or ("数据已过期" if status=="stale" else None),
          "provider":provider,"capability":capability,"subject":subject,"observed_at":row.observed_at,
          "received_at":row.received_at,"expires_at":row.expires_at,"next_refresh_at":row.next_refresh_at,
          "raw_ref":row.raw_ref,"last_success_at":row.last_success_at,
          "last_success_raw_ref":row.last_success_raw_ref,
          "last_attempt_at":row.last_attempt_at,"last_attempt_raw_ref":row.last_attempt_raw_ref,
          "policy":policy,"payload":row.payload}
def budget_status(config):
    with Session() as s:
        result=[]
        for p,c in (("cmc",config.sources.cmc),("bykaranteli",config.sources.bykaranteli)):
            r=s.get(ProviderBudget,(p,month_key()));local=r.used if r else 0
            account=s.get(UserPreference,'account_usage:'+p)
            report=account.payload if account and account.payload.get('month')==month_key() else {}
            effective=max(local+c.external_used,report.get('credits_used',0)+max(0,local-report.get('local_baseline',0)))
            result.append({'provider':p,'month':month_key(),'used':local,'effective_used':effective,
                'external_used':c.external_used,'limit':min(c.month_budget,report.get('monthly_limit',c.month_budget)),
                'reserve_pct':max(10,c.reserve_pct),'blocked_until':r.blocked_until if r else None,
                'account_observed_at':account.updated_at if report else None,
                'usage_scope':'account_snapshot_plus_local_delta' if report else 'local_ledger_only_account_remaining_unknown'})
        return result


def budget_plan(config, map_count, now=None):
    dt=datetime.fromtimestamp((now or now_ms())/1000,timezone.utc)
    days=calendar.monthrange(dt.year,dt.month)[1]
    remaining_days=days-dt.day+1
    used={r["provider"]:r for r in budget_status(config)}
    result={}
    for provider,cfg in (("cmc",config.sources.cmc),("bykaranteli",config.sources.bykaranteli)):
        actual=used[provider]["effective_used"]
        limit=used[provider]['limit']
        fraction=actual/limit
        tier="stopped" if fraction>=.95 else "critical_only" if fraction>=.85 else "reduced" if fraction>=.70 else "normal"
        available=max(0,int(limit*(1-max(10,cfg.reserve_pct)/100))-actual)
        result[provider]={"month_days":days,"remaining_days":remaining_days,"available":available,
          "daily_remaining":available//remaining_days,"tier":tier,"used_pct":round(fraction*100,2)}
    daily_shared=12+24+24+48+4+4+12*4
    result["bykaranteli"]["map_count"]=map_count
    result["bykaranteli"]["baseline_month_requests"]=(daily_shared+map_count)*days
    result["bykaranteli"]["fits_baseline"]=result["bykaranteli"]["daily_remaining"]>=daily_shared+map_count
    return result
