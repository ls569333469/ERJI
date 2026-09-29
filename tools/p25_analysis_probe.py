import asyncio,json
from engine.repository import bootstrap,save_batch
from engine.adapters.cex import LinearPerpAdapter
from engine.signal_service import scan_signals
from engine.pd2.service import decision_list,analyze_market
async def main():
 bootstrap();a=LinearPerpAdapter('binance')
 try:
  b=await a.collect(['BTC'],history=False);save_batch(b)
  print(json.dumps({'btc_states':len(b.states),'errors':b.errors}))
 finally:await a.close()
 print(json.dumps({'scan':scan_signals(),'signals':len(decision_list()['rows'])}))
 a=analyze_market('binance:perp:BTCUSDT')
 print(json.dumps({'real_analysis':[(r['timeframe'],r['status'],r['bar_count']) for r in a['timeframes']]}))
asyncio.run(main())
