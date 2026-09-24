import asyncio, hashlib, json, time, ssl
from dataclasses import dataclass, field
from decimal import Decimal
import httpx

def now_ms(): return time.time_ns()//1_000_000
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

class PublicAdapter:
    venue: str
    def __init__(self):
        self.client=httpx.AsyncClient(timeout=20, follow_redirects=False, verify=ssl.create_default_context(), transport=httpx.AsyncHTTPTransport(retries=2,verify=ssl.create_default_context()))
        self.lock=asyncio.Lock()
        self.slots=asyncio.Semaphore(4)
        self.last_request=0.0
        self.blocked_until=0.0
    async def close(self): await self.client.aclose()
    async def request(self,batch,method,url,**kwargs):
        async with self.slots:
            async with self.lock:
                await asyncio.sleep(max(0,self.blocked_until-time.monotonic(),0.25-(time.monotonic()-self.last_request)))
                self.last_request=time.monotonic()
            try:
                response=await self.client.request(method,url,**kwargs)
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
