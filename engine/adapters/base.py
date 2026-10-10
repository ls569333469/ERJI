import asyncio, hashlib, json, time, ssl, weakref
from dataclasses import dataclass, field
from decimal import Decimal
import httpx
from urllib.request import getproxies

def now_ms(): return time.time_ns()//1_000_000
def public_error(error):
    # Preserve the useful HTTP status without exposing URLs, headers or bodies.
    if isinstance(error,httpx.HTTPStatusError):
        code=error.response.status_code
        return "HTTP 451（来源拒绝当前服务器访问）" if code==451 else f"HTTP {code}"
    return type(error).__name__

def decimal_text(value):
    result=Decimal(str(value))
    if not result.is_finite(): raise ValueError("non-finite market number")
    return format(result,"f")

@dataclass
class Batch:
    venue: str
    instruments: list[dict] = field(default_factory=list)
    states: list[dict] = field(default_factory=list)
    candles: list[dict] = field(default_factory=list)
    oi: list[dict] = field(default_factory=list)
    funding: list[dict] = field(default_factory=list)
    raw: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    candle_only: bool = False

_loops=weakref.WeakKeyDictionary()
def shared_http_slots():
    loop=asyncio.get_running_loop()
    if loop not in _loops: _loops[loop]=asyncio.Semaphore(4)
    return _loops[loop]

class PublicAdapter:
    venue: str
    def __init__(self):
        self.client=httpx.AsyncClient(timeout=20, follow_redirects=False, verify=ssl.create_default_context(), transport=httpx.AsyncHTTPTransport(retries=0,verify=ssl.create_default_context(),proxy=getproxies().get('https')))
        self.lock=asyncio.Lock()
        self.slots=asyncio.Semaphore(4)
        self.last_request=0.0
        self.blocked_until=0.0
    async def close(self): await self.client.aclose()
    async def request(self,batch,method,url,**kwargs):
        from engine.pd2.source_policy import assert_market_request
        assert_market_request(url, batch.venue)
        from engine.db import owner_context
        owner=owner_context.get()
        if owner is not None and owner.lost:raise RuntimeError('worker_role_lost')
        from engine.pd2.p36_budget import specification, sent, durable_call, feedback as modern_feedback
        from engine.pd2.request_budget import request_slot, feedback
        if specification(url,kwargs):
            async with request_slot(url,kwargs) as ticket:
                await durable_call(sent,ticket)
                network_started=time.monotonic()
                async with asyncio.timeout(45):
                    response=await self.client.request(method,url,**kwargs)
                received=now_ms()
                batch.metadata['last_network_ms']=round((time.monotonic()-network_started)*1000)
                await durable_call(modern_feedback,url,response.status_code,dict(response.headers),kwargs,ticket,received,round((time.monotonic()-network_started)*1000))
                response.raise_for_status()
                data=response.json()
                if isinstance(data,dict) and isinstance(data.get('code'),int) and data['code']<0:
                    raise ValueError('provider_business_error')
                raw_text=json.dumps(data,separators=(',',':'),sort_keys=True)
                raw_id=hashlib.sha256((self.venue+url+str(received)+raw_text).encode()).hexdigest()
                batch.raw.append({'id':raw_id,'source':self.venue,'endpoint':url.split('?')[0],'received_at':received,'payload':data})
                return data,raw_id,received
        return await self._legacy_request(batch,method,url,**kwargs)

    async def _legacy_request(self,batch,method,url,**kwargs):
        from engine.pd2.source_policy import assert_market_request
        assert_market_request(url,batch.venue)
        from engine.pd2.request_budget import request_slot, feedback
        async with request_slot(url, kwargs), shared_http_slots(), self.slots:
            async with self.lock:
                await asyncio.sleep(max(0,self.blocked_until-time.monotonic(),0.25-(time.monotonic()-self.last_request)))
                self.last_request=time.monotonic()
            try:
                network_started=time.monotonic()
                response=await self.client.request(method,url,**kwargs)
                batch.metadata['last_network_ms']=round((time.monotonic()-network_started)*1000)
                await asyncio.to_thread(feedback,url,response.status_code,response.headers)
                if response.status_code in (418,429):
                    try: delay=float(response.headers.get("Retry-After","60"))
                    except ValueError: delay=60
                    self.blocked_until=time.monotonic()+max(60,min(delay,3600))
                response.raise_for_status()
                data=response.json()
                if isinstance(data,dict) and isinstance(data.get("code"),int) and data["code"]<0:
                    raise ValueError("provider_business_error")
                raw_text=json.dumps(data,separators=(",",":"),sort_keys=True)
                received=now_ms()
                raw_id=hashlib.sha256((self.venue+url+str(received)+raw_text).encode()).hexdigest()
                batch.raw.append({"id":raw_id,"source":self.venue,"endpoint":url.split("?")[0],"received_at":received,"payload":data})
                return data,raw_id,received
            finally: pass
