import pd2_env
import asyncio,json
from pathlib import Path
from engine.pd2.native_streams import stream_venue
from engine.db import Session
from engine.models import WorkerHeartbeat
async def main():
 await asyncio.gather(stream_venue('binance',12),stream_venue('aster',12))
 with Session() as s:
  results=[{'source':v,'state':r.state,'details':r.details,'time':r.updated_at} for v in ('binance','aster') if (r:=s.get(WorkerHeartbeat,'stream_'+v))]
 Path('docs/pd2-stream-probe.json').write_text(json.dumps(results),encoding='utf8')
 print(json.dumps(results),flush=True)
asyncio.run(main())
