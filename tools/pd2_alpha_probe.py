import pd2_env
import asyncio,json
from engine.repository import bootstrap,save_batch
from engine.contracts import RuntimeConfig
from engine.adapters.expanded_markets import BinanceCashAdapter
from engine.adapters.wallet import WalletAdapter
bootstrap()
async def run(a):
 try:
  b=await a.collect(RuntimeConfig(asset_limit=10,deep_asset_limit=10).model_copy(update={"deep_asset_limit":1}));save_batch(b)
  return {'source':a.venue,'instruments':len(b.instruments),'states':len(b.states),'bars':len(b.candles),'errors':b.errors[:10],'metadata':b.metadata}
 except Exception as e:return {'source':a.venue,'error':type(e).__name__,'detail':str(getattr(e,'orig',e))[:200]}
 finally:await a.close()
async def main():
 results=await asyncio.gather(*(run(a) for a in [BinanceCashAdapter(True)]))
 from pathlib import Path
 Path("docs/pd2-alpha-probe.json").write_text(json.dumps(results,ensure_ascii=False),encoding="utf8")
 print(json.dumps(results,ensure_ascii=False),flush=True)
asyncio.run(main())
