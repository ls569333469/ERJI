"""Public 25-second WS probe; run through p25_verify.py in the local test schema."""
import asyncio,json
from sqlalchemy import select,func
from engine.db import Session
from engine.models import WorkerHeartbeat,RawEvent,MarketState,DataCapability
from engine.adapters.base import now_ms
from engine.pd2.native_streams import stream_venue,stream_hyperliquid

async def main():
    started=now_ms()
    await asyncio.gather(*(stream_venue(v,stop_after=25) for v in ('binance','aster','binance_spot')),stream_hyperliquid(stop_after=25))
    with Session() as s:
        health=[{'role':r.role,'state':r.state,'details':r.details} for r in s.scalars(select(WorkerHeartbeat).where(WorkerHeartbeat.role.like('stream_%'),WorkerHeartbeat.updated_at>=started))]
        facts=dict(s.execute(select(RawEvent.endpoint,func.count()).where(RawEvent.received_at>=started,RawEvent.endpoint.like('ws:%')).group_by(RawEvent.endpoint)).all())
        quotes=s.scalar(select(func.count()).select_from(MarketState).where(MarketState.as_of>=started))
        books=s.scalar(select(func.count()).select_from(DataCapability).where(DataCapability.capability=='execution_book',DataCapability.received_at>=started))
    print(json.dumps({'health':health,'facts':facts,'new_quotes':quotes,'new_books':books},ensure_ascii=False))

asyncio.run(main())
