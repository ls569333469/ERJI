"""Documented read-only supplemental providers; secret material never enters payloads."""
import asyncio,json,re,ssl
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal as D
import httpx
from urllib.request import getproxies
from sqlalchemy import select
from engine.config import settings
from engine.db import Session
from engine.models import IdentityLink,Instrument
from engine.repository import runtime_config
from engine.adapters.base import now_ms
from engine.pd2.storage import get,put,reserve,reconcile,budget_plan

def timestamp(value):
    if isinstance(value,(int,float)):
        return int(value if value>100000000000 else value*1000)
    if isinstance(value,str):
        try:return int(datetime.fromisoformat(value.replace("Z","+00:00")).timestamp()*1000)
        except ValueError:return None
    return None

def read_key(path):
    if not path:return None
    content=Path(path).read_text(encoding="utf-8-sig").strip()
    try:
        obj=json.loads(content)
        if isinstance(obj,dict):
            for name in ("api_key","apiKey","key","token","bearer"):
                if isinstance(obj.get(name),str):return obj[name].removeprefix("Bearer ").strip()
    except json.JSONDecodeError:pass
    match=re.search(r"(?:Bearer\s+|(?:api[_ -]?key|token|key)\s*[:=：]\s*)[\"']?([A-Za-z0-9_.:-]{20,})",content,re.I)
    if match:return match.group(1)
    candidates=[line.strip() for line in content.splitlines() if re.fullmatch(r"[A-Za-z0-9_.:-]{20,}",line.strip())]
    if len(candidates)==1:return candidates[0]
    if re.fullmatch(r"[A-Za-z0-9_.:-]{20,}",content):return content
    raise ValueError("secret_file_format_unsupported")

class Supplemental:
    def __init__(self):
        self.client=httpx.AsyncClient(timeout=20,follow_redirects=False,verify=ssl.create_default_context(),proxy=getproxies().get('https'))
        self.lock=asyncio.Lock()
    async def close(self):await self.client.aclose()
    async def request(self,provider,capability,subject,path,params=None,ttl=300000,refresh=1800000,priority="normal"):
        from engine.pd2.tasks import claim,finish
        lease=claim('supplement',provider+':'+capability+':'+subject)
        if not lease:return get(provider,capability,subject)
        error=None
        try:return await self._request(provider,capability,subject,path,params,ttl,refresh,priority)
        except Exception as e:error=type(e).__name__;raise
        finally:finish(lease,error)
    async def _request(self,provider,capability,subject,path,params=None,ttl=300000,refresh=1800000,priority="normal"):
        config=runtime_config();cfg=getattr(config.sources,provider)
        cached=get(provider,capability,subject)
        # Critical active requests can advance a daily background refresh deadline.
        plan=budget_plan(config,0)[provider]
        if plan["tier"]=="reduced":refresh*=2
        due=(cached.get("received_at",0)+refresh) if priority=="critical" else max(cached.get("next_refresh_at") or 0,cached.get("received_at",0)+refresh)
        if due>now_ms():return cached
        if not cfg.enabled:
            put(provider,capability,subject,{},None,0,status="disabled",reason="设置中已关闭",next_ms=now_ms()+60000)
            return get(provider,capability,subject)
        secret_path=getattr(settings(),provider+"_key_file")
        try:key=read_key(secret_path)
        except (OSError,ValueError):key=None
        if not key:
            put(provider,capability,subject,{},None,0,status="missing_key",reason="未配置或无法读取本地Key",next_ms=now_ms()+60000)
            return get(provider,capability,subject)
        allowed,reason=reserve(provider,cfg,priority=priority)
        if not allowed:
            put(provider,capability,subject,{},None,0,status=reason,reason="共享免费额度或限流保护",next_ms=now_ms()+60000)
            return get(provider,capability,subject)
        base="https://pro-api.coinmarketcap.com" if provider=="cmc" else "https://bykaranteli.com"
        headers={"X-CMC_PRO_API_KEY":key} if provider=="cmc" else {"Authorization":"Bearer "+key}
        from engine.pd2.request_budget import request_slot, feedback
        async with self.lock, request_slot(base+path, {}):
            try:
                response=await self.client.get(base+path,params=params,headers=headers)
                await asyncio.to_thread(feedback,base+path,response.status_code,response.headers)
                if response.status_code==429:
                    try:retry=float(response.headers.get("retry-after","60"))
                    except ValueError:retry=60
                    reconcile(provider,retry_after=max(60,min(retry,3600)))
                response.raise_for_status();data=response.json()
                if provider=="cmc":
                    status=data.get("status",{})
                    reconcile(provider,extra=max(0,int(status.get("credit_count",1))-1))
                    if str(status.get("error_code",0)) not in ("0","None",""):raise ValueError("provider_business_error_"+str(status.get("error_code")))
                    observed=timestamp(status.get("timestamp"))
                else:
                    if data.get("ok") is False:raise ValueError("provider_business_error")
                    observed=timestamp(data.get("as_of") or data.get("updatedAt") or data.get("updated_at"))
                    # generatedAt is response creation, never accepted as market timestamp.
                put(provider,capability,subject,data,observed,ttl,endpoint=path,next_ms=now_ms()+refresh)
            except httpx.HTTPStatusError as error:
                code=error.response.status_code
                status="unsupported" if code==400 else "permission_denied" if code in (401,403) else "rate_limited" if code==429 else "source_error"
                put(provider,capability,subject,{},None,0,status=status,reason=f"来源HTTP {code}",endpoint=path,next_ms=now_ms()+refresh)
            except Exception as error:
                put(provider,capability,subject,{},None,0,status="source_error",reason=type(error).__name__,endpoint=path,next_ms=now_ms()+60000)
            await asyncio.sleep(2.1)
        return get(provider,capability,subject)

    async def cmc(self):
        from engine.pd2.storage import account_usage
        account=await self.request('cmc','account','global','/v1/key/info',ttl=900000,refresh=900000,priority='critical')
        if account.get('status')=='fresh' and account.get('payload'):account_usage('cmc',account['payload'],account['received_at'])
        from engine.pd2.identity import REVIEWED,save_map
        mapping=await self.request("cmc","identity_catalog","reviewed","/v1/cryptocurrency/map",{"symbol":",".join(REVIEWED),"listing_status":"active"},ttl=21600000,refresh=21600000)
        if mapping.get("payload"):save_map(mapping["payload"])
        with Session() as s:
            links=list(s.scalars(select(IdentityLink).where(IdentityLink.provider=="cmc",IdentityLink.status=="verified")))
        from engine.models import CurrentSignal
        with Session() as s:active=set(s.scalars(select(CurrentSignal.asset_id).where(CurrentSignal.action!='ended')))
        active_ids=set(v.external_id for v in links if v.asset_id in active)|{'825','3408'}
        background=set(v.external_id for v in links)-active_ids
        quote_tasks=[]
        for ids,interval,priority in ((sorted(active_ids),1800000,'critical'),(sorted(background),21600000,'background')):
            quote_tasks.extend((ids[start:start+100],interval,priority) for start in range(0,len(ids),100))
        for chosen,interval,priority in quote_tasks:
            result=await self.request("cmc","quotes",__import__("hashlib").sha256(",".join(chosen).encode()).hexdigest()[:32],"/v2/cryptocurrency/quotes/latest",{"id":",".join(chosen),"convert":"USD"},ttl=86400000,refresh=interval,priority=priority)
            if not result.get("payload"):continue
            for ident,entry in (result["payload"].get("data") or {}).items():
                quote=entry.get("quote",{}).get("USD",{})
                observed=timestamp(quote.get("last_updated") or entry.get("last_updated"))
                payload={"id":str(entry.get("id",ident)),"symbol":entry.get("symbol"),"price":quote.get("price"),
                    "circulating_supply":entry.get("circulating_supply"),"market_cap":quote.get("market_cap"),
                    "observed_at":observed,"kind":"reported_circulating_not_fdv"}
                put("cmc","valuation",str(ident),payload,observed,86400000,endpoint="/v2/cryptocurrency/quotes/latest")
        # Per-asset derivatives are rotated; free budget is shared with quotes.
        if links:
            ordered=sorted(links,key=lambda v:get("cmc","derivatives",v.external_id).get("received_at",0))
            for link in ordered[:5]:
                await self.request("cmc","derivatives",link.external_id,"/v5/cryptocurrency/derivatives/market-pairs/list/latest",
                  {"crypto_id":link.external_id,"category":"perpetual","convert":"USD","limit":500},ttl=300000,refresh=1800000)
            await self.request("cmc","liquidations","global","/v5/derivatives/liquidations/cryptocurrency/list/latest",
                  {"symbol":",".join(v.asset_id for v in ordered[:5]),"convert":"USD"},ttl=300000,refresh=1800000)
    async def bykaranteli(self):
        specs=[("symbols","/api/public/symbols",{"top":1000},21600000),
          ("coverage","/api/public/coverage",None,21600000),
          ("oi_history","/api/public/venues/oi-history",None,21600000),
          ("pressure","/api/public/pressure",{"limit":200},600000),("oi","/api/public/oi",None,600000),
          ("funding","/api/public/funding-arb",None,600000),("slippage","/api/public/slippage",None,7200000),
          ("markets","/api/public/venues/markets",None,3600000),
          ("liquidation_leaderboard","/api/public/liquidation-leaderboard",{"window":"24h","limit":200},3600000),
          ("whales","/api/public/hyperliquid-whales",{"events":1},1800000),
          ("liquidations","/api/public/liquidations",None,1800000)]
        for cap,path,params,interval in specs:
            await self.request("bykaranteli",cap,"global",path,params,ttl=300000,refresh=interval)
        await self.map_rotation()


    async def map_rotation(self):
        from engine.pd2.catalog import map_symbols
        from engine.models import DataCapability,UserPreference,Campaign,Episode
        config=runtime_config();catalog=get('bykaranteli','map_catalog','global')
        if catalog.get('received_at',0)+21600000<=now_ms():
            try:
                from engine.pd2.request_budget import request_slot
                async with request_slot('https://bykaranteli.com/liqmap',{}):
                    response=await self.client.get('https://bykaranteli.com/liqmap');response.raise_for_status()
                symbols=map_symbols(response.text)
                with Session() as s:
                    native=set(s.scalars(select(Instrument.asset_id).where(Instrument.venue=='binance',Instrument.market_type=='linear_perpetual',Instrument.quote_asset=='USDT')))
                supported=sorted(set(symbols)&native)
                put('bykaranteli','map_catalog','global',{'symbols':symbols,'crypto_symbols':supported,'excluded_nonmatching':sorted(set(symbols)-native),'verification':'catalog_only_each_response_verified_separately','count':len(symbols)},now_ms(),21600000)
                catalog=get('bykaranteli','map_catalog','global')
            except Exception as e:
                put('bykaranteli','map_catalog','global',{},None,0,status='source_error',reason=type(e).__name__)
        symbols=(catalog.get('payload') or {}).get('crypto_symbols',[])
        if not symbols:return
        with Session() as s:
            records={r.subject:r for r in s.scalars(select(DataCapability).where(DataCapability.provider=='bykaranteli',DataCapability.capability=='liqmap'))}
            pace=s.get(UserPreference,'map_rotation');last=(pace.payload if pace else {}).get('last',0)
            active=set(s.scalars(select(Instrument.asset_id).join(Campaign,Campaign.reference_instrument_id==Instrument.id).join(Episode).where(Instrument.venue=='binance',Episode.phase.notin_(['ended','invalidated']))))
        now=now_ms();spacing=max(60000,86400000//len(symbols))
        # Active maps have their own deadline, independent of the daily background rotation.
        active_due=sorted((x for x in active & set(symbols) if x not in records or now-records[x].received_at>=1800000),key=lambda x:records[x].received_at if x in records else 0)
        for symbol in active_due[:2]:
            await self.request('bykaranteli','liqmap',symbol,'/api/liqmap/public',{'symbol':symbol},ttl=300000,refresh=1800000,priority='critical')
        if now-last<spacing:return
        due=sorted([x for x in symbols if x not in records or (records[x].next_refresh_at or 0)<=now],key=lambda x:((records[x].received_at if x in records else 0),x))
        if not due:return
        chosen=due[0]
        await self.request('bykaranteli','liqmap',chosen,'/api/liqmap/public',{'symbol':chosen},ttl=300000,refresh=86400000,priority='background')
        with Session.begin() as s:
            s.merge(UserPreference(key='map_rotation',updated_at=now,payload={'last':now,'catalog_count':len(symbols),'queued':len(due),'oldest_wait_ms':max([now-r.received_at for r in records.values()] or [0]),'daily_spacing_ms':spacing}))

def seed_verified_identity():
    # Explicit identities already verified in local source research, not fuzzy ticker matching.
    known={"BTC":"1","ETH":"1027","SOL":"5426","HYPE":"32196","ASTER":"36341","RAVE":"38967"}
    with Session.begin() as s:
        for asset,external in known.items():
            if not s.get(IdentityLink,(asset,"cmc")):
                s.add(IdentityLink(asset_id=asset,provider="cmc",external_id=external,status="verified",
                    evidence={"method":"documented_identity","source":"PD1 CMC Sep23 and canonical established assets","multiplier":"1"},known_at=now_ms()))

def valuation_inputs(markets, session):
    """Two bounded queries per snapshot, including negative identity lookups."""
    from engine.models import DataCapability
    assets={m.asset_id for m in markets}
    links={r.asset_id:r for r in session.scalars(select(IdentityLink).where(
        IdentityLink.asset_id.in_(assets),IdentityLink.provider=="cmc"))}
    keys={"cmc:valuation:"+str(r.external_id) for r in links.values() if r.status=="verified"}
    keys.update(("cmc:valuation:825","cmc:valuation:3408"))
    records={r.key:r for r in session.scalars(select(DataCapability).where(DataCapability.key.in_(keys)))}
    return links,records


def valuation_for(market,session,now,inputs=None):
    link=inputs[0].get(market.asset_id) if inputs is not None else session.get(IdentityLink,(market.asset_id,"cmc"))
    if not link or link.status!="verified":return {"status":"unmapped","cap":None,"ratio":None,"usd":None}
    def current(cap,subject,age):
        from engine.models import DataCapability
        key=f"cmc:{cap}:{subject}"
        r=inputs[1].get(key) if inputs is not None else session.get(DataCapability,key)
        return r.payload if r and r.status=="fresh" and r.observed_at and 0<=now-r.observed_at<=age else None
    supply=current("valuation",link.external_id,86400000)
    fx={"USD":D(1)}
    for unit,ident in (("USDT","825"),("USDC","3408")):
        q=current("valuation",ident,3600000)
        if q and q.get("price"):fx[unit]=D(str(q["price"]))
    if not supply or supply.get("circulating_supply") is None:return {"status":"supply_missing_or_stale","cap":None,"ratio":None,"usd":None}
    if market.quote_asset not in fx:return {"status":"fx_missing_or_stale","cap":None,"ratio":None,"usd":None}
    if market.price is None or market.quality.status!="fresh":return {"status":"price_stale","cap":None,"ratio":None,"usd":None}
    multiplier=D(str(link.evidence.get("multiplier","1")))
    cap=D(str(supply["circulating_supply"]))*market.price/multiplier*fx[market.quote_asset]
    usd=market.oi_notional_quote*fx[market.quote_asset] if market.oi_notional_quote is not None else None
    return {"status":"fresh" if cap>0 else "invalid_supply","cap":cap if cap>0 else None,
      "usd":usd,"ratio":usd/cap if cap>0 and usd is not None and market.oi_quality.status=="fresh" else None,
      "cap_method":"circulating_supply * contemporaneous_native_price * fx","source":"CMC supply + native quote",
      "supply_time":supply["observed_at"],"coverage":"this_instrument"}


def quote_usd_rate(quote,session,now):
    if quote=="USD":return D(1)
    ident={"USDT":"825","USDC":"3408"}.get(quote)
    if not ident:return None
    from engine.models import DataCapability
    row=session.get(DataCapability,f"cmc:valuation:{ident}")
    if not row or row.status!="fresh" or not row.observed_at or not 0<=now-row.observed_at<=3600000:return None
    price=row.payload.get("price")
    return D(str(price)) if price is not None and D(str(price))>0 else None
