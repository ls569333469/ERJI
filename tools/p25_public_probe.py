import asyncio,json
from engine.adapters.binance_universe import BinanceUniverseAdapter
from engine.adapters.base import public_error
from engine.repository import bootstrap,save_batch
from engine.contracts import RuntimeConfig
from engine.pd2.collection_schedule import acknowledge_batch
async def main():
 bootstrap();a=BinanceUniverseAdapter()
 try:
  batch=await a.collect(RuntimeConfig());save_batch(batch);acknowledge_batch(batch)
  print(json.dumps({'source':'binance','catalog':len(batch.instruments),'states':len(batch.states),'oi':len(batch.oi),'errors':batch.errors[:5]},ensure_ascii=False))
  b=await a.collect_candles([('binance:perp:BTCUSDT','BTCUSDT',r,i,300) for r,i in [('60','1h'),('240','4h'),('1D','1d'),('1W','1w')]])
  save_batch(b);print(json.dumps({'candles':len(b.candles),'errors':b.errors},ensure_ascii=False))
 except Exception as e:print(json.dumps({'probe_error':public_error(e)}));raise
 finally:await a.close()
asyncio.run(main())
