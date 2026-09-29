"""Public venue books; aggregate vendor books are never treated as executable."""
from engine.adapters.base import PublicAdapter,Batch,now_ms
from engine.pd2.storage import put
from engine.models import Instrument
from engine.db import Session

class ExecutionAdapter(PublicAdapter):
    venue="execution"
    async def collect(self,instrument_id):
        with Session() as s:
            inst=s.get(Instrument,instrument_id)
            if not inst:return
            venue,native=inst.venue,inst.native_symbol
            multiplier=inst.multiplier
        batch=Batch(venue)
        try:
            if venue in ("binance","aster"):
                base="https://fapi.binance.com/fapi/v1" if venue=="binance" else "https://fapi.asterdex.com/fapi/v3"
                data,ref,received=await self.request(batch,"GET",base+"/depth",params={"symbol":native,"limit":100})
                observed=int(data.get("T") or data.get("E") or received)
                bids,asks=data["bids"],data["asks"]
            elif venue in ("binance_spot","binance_alpha"):
                base="https://api.binance.com/api/v3/depth" if venue=="binance_spot" else "https://www.binance.com/bapi/defi/v1/public/alpha-trade/fullDepth"
                data,ref,received=await self.request(batch,"GET",base,params={"symbol":native,"limit":100})
                if venue=="binance_alpha":data=data["data"]
                observed=int(data.get("E") or data.get("T") or received);bids,asks=data["bids"],data["asks"]
            elif venue=="okx":
                data,ref,received=await self.request(batch,"GET","https://www.okx.com/api/v5/market/books",params={"instId":native,"sz":100})
                data=data["data"][0];observed=int(data["ts"])
                from decimal import Decimal
                bids=[[r[0],str(Decimal(r[1])*multiplier)] for r in data["bids"]]
                asks=[[r[0],str(Decimal(r[1])*multiplier)] for r in data["asks"]]
            elif venue=="hyperliquid":
                data,ref,received=await self.request(batch,"POST","https://api.hyperliquid.xyz/info",json={"type":"l2Book","coin":native})
                observed=int(data["time"])
                bids=[[v["px"],v["sz"]] for v in data["levels"][0]]
                asks=[[v["px"],v["sz"]] for v in data["levels"][1]]
            else:
                put(venue,"execution_book",instrument_id,{},None,0,status="unsupported",reason="尚无该市场原生执行盘口适配器")
                return
            payload={"bids":bids,"asks":asks,"received_at":received,"observed_at":observed,
              "scope":"execution_market","instrument_id":instrument_id,"quote_kind":"ask"}
            put(venue,"execution_book",instrument_id,payload,observed,10000,endpoint="public depth")
        except Exception as error:
            put(venue,"execution_book",instrument_id,{},None,0,status="source_error",reason=type(error).__name__)
