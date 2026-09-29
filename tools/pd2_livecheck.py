import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import os,sys,json,asyncio
from sqlalchemy.engine import make_url
from engine.config import Settings
src=Settings(_env_file="D:/Axiangmu/erji/.env")
os.environ["ERJI_DATABASE_URL"]=make_url(src.database_url).update_query_dict({"options":"-csearch_path=erji_pd2_20260924"}).render_as_string(hide_password=False)
os.environ["ERJI_CMC_KEY_FILE"]="F:/key/CMC.txt"
os.environ["ERJI_BYKARANTELI_KEY_FILE"]="F:/key/bykaranteli.txt"
from engine.repository import bootstrap,save_batch,heartbeat
from engine.contracts import RuntimeConfig
bootstrap()
from engine.pd2.providers import Supplemental,seed_verified_identity
from engine.adapters.hyperliquid import HyperliquidAdapter
from engine.adapters.cex import LinearPerpAdapter
from engine.pd2.execution import ExecutionAdapter
seed_verified_identity()
async def native(adapter):
 try:
  batch=await adapter.collect(RuntimeConfig(asset_limit=10,deep_asset_limit=10),history=True)
  save_batch(batch)
  print(json.dumps({"source":adapter.venue,"instruments":len(batch.instruments),"states":len(batch.states),"candles":len(batch.candles),"errors":batch.errors[:4]}),flush=True)
 except Exception as e:print(json.dumps({"source":adapter.venue,"error":type(e).__name__,"detail":str(getattr(e,"orig",""))[:200]}),flush=True)
 finally:await adapter.close()
async def supplement():
 a=Supplemental()
 try:
  await a.cmc()
  await a.bykaranteli()
  await a.request("bykaranteli","liqmap","BTC","/api/liqmap/public",{"symbol":"BTC"},refresh=1800000)
 finally:await a.close()
async def main():
 await native(HyperliquidAdapter())
 await native(LinearPerpAdapter("aster"))
 await supplement()
 a=ExecutionAdapter()
 try:await a.collect("binance:perp:BTCUSDT")
 finally:await a.close()
 from engine.pd2.service import refresh_plans
 print(json.dumps(refresh_plans()),flush=True)
 from engine.pd2.storage import budget_status
 from engine.db import Session
 from engine.models import DataCapability
 from sqlalchemy import select
 with Session() as s:
  print(json.dumps([{"provider":r.provider,"capability":r.capability,"subject":r.subject,"status":r.status,"reason":r.reason,"fields":list(r.payload)[:10]} for r in s.scalars(select(DataCapability))]),flush=True)
 print(json.dumps(budget_status(RuntimeConfig())),flush=True)
asyncio.run(main())
