"""Public venue books; aggregate vendor books are never treated as executable."""
from engine.adapters.base import PublicAdapter,Batch,now_ms
from engine.pd2.storage import put
from engine.models import Instrument
from engine.db import Session


def needs_refresh(cached,now):
    status=cached.get('status');received=cached.get('received_at') or 0
    from engine.pd2.book_clock import fresh
    if status=='fresh' and 0<=now-received<5000 and fresh(cached.get('payload') or cached,now):return False
    attempted=cached.get('last_attempt_at') or received
    # Missing source timestamps cannot become actionable by polling the same
    # unsupported response every few seconds. Retain a bounded capability retry.
    delay=300000 if status in ('source_time_unknown','unsupported') else 30000 if status in ('source_error','rate_limited') else 0
    return not (delay and 0<=now-attempted<delay)


class ExecutionAdapter(PublicAdapter):
    venue="execution"
    async def collect(self,instrument_id):
        with Session() as s:
            inst=s.get(Instrument,instrument_id)
            if not inst:return
            venue,native=inst.venue,inst.native_symbol
        from engine.pd2.source_policy import REALTIME
        if venue not in REALTIME:return
        from engine.pd2.storage import get
        cached=get(venue,'execution_book',instrument_id)
        if not needs_refresh(cached,now_ms()):
            return
        batch=Batch(venue)
        try:
            if venue=="binance":
                base="https://fapi.binance.com/fapi/v1"
                data,ref,received=await self.request(batch,"GET",base+"/depth",params={"symbol":native,"limit":100})
                observed=int(data.get("T") or data.get("E") or 0) or None
                bids,asks=data["bids"],data["asks"]
            elif venue in ("binance_spot","binance_alpha"):
                base="https://data-api.binance.vision/api/v3/depth" if venue=="binance_spot" else "https://www.binance.com/bapi/defi/v1/public/alpha-trade/fullDepth"
                data,ref,received=await self.request(batch,"GET",base,params={"symbol":native,"limit":100})
                if venue=="binance_alpha":data=data["data"]
                observed=int(data.get("E") or data.get("T") or 0) or None;bids,asks=data["bids"],data["asks"]
            else:
                put(venue,"execution_book",instrument_id,{},None,0,status="unsupported",reason="尚无该市场原生执行盘口适配器")
                return
            from engine.models import RawEvent
            with Session.begin() as s:
                for raw in batch.raw:s.merge(RawEvent(**raw))
            payload={"bids":bids,"asks":asks,"received_at":received,"observed_at":observed,'source_raw_ref':ref,
              "scope":"execution_market","instrument_id":instrument_id,"quote_kind":"ask"}
            payload.update(venue=venue,source_time_kind='exchange' if observed is not None else 'unknown',roundtrip_ms=batch.metadata.get('last_network_ms'))
            if venue=='binance_spot' and observed is None:
                payload.update(source_time_kind='snapshot_received',last_update_id=data.get('lastUpdateId'))
            put(venue,"execution_book",instrument_id,payload,observed,90000,endpoint="public depth")
        except Exception as error:
            put(venue,"execution_book",instrument_id,{},None,0,status="source_error",reason=type(error).__name__)
