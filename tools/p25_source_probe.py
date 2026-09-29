import asyncio,json
from engine.adapters.base import PublicAdapter,Batch,public_error
from engine.repository import bootstrap,save_batch
from engine.adapters.hyperliquid import HyperliquidAdapter
from engine.adapters.cex import LinearPerpAdapter
from engine.contracts import RuntimeConfig
from engine.pd2.providers import seed_verified_identity,read_key
import httpx,urllib.request
async def main():
 bootstrap();seed_verified_identity()
 a=PublicAdapter();a.venue='binance_alpha'
 try:
  data,_,_=await a.request(Batch(a.venue),'GET','https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list')
  rows=data.get('data') or [];sample=next(iter(rows),{})
  print(json.dumps({'alpha_token_count':len(rows),'fields':list(sample),'valuation_fields':{k:v for k,v in sample.items() if any(w in k.lower() for w in ['cap','liquid','supply','volume'])}},ensure_ascii=False))
 except Exception as e:print('alpha',public_error(e))
 finally:await a.close()
 for cls in (HyperliquidAdapter,lambda:LinearPerpAdapter('aster')):
  a=cls()
  try:
   b=await a.collect(RuntimeConfig(),history=False);save_batch(b)
   b=await a.collect(RuntimeConfig(),history=False);save_batch(b)
   print(json.dumps({'source':a.venue,'catalog':len(b.instruments),'matched_oi':len(b.oi),'errors':b.errors[:3]}))
  except Exception as e:print(a.venue,public_error(e))
  finally:await a.close()
 key=read_key('F:/key/CMC.txt')
 async with httpx.AsyncClient(proxy=urllib.request.getproxies().get('https'),timeout=20) as c:
  r=await c.get('https://pro-api.coinmarketcap.com/v1/key/info',headers={'X-CMC_PRO_API_KEY':key})
  d=r.json().get('data',{});print(json.dumps({'cmc_key_info_http':r.status_code,'usage':d.get('usage'),'plan_fields':list(d.get('plan',{}))}))
asyncio.run(main())
