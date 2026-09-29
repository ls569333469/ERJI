import asyncio,json,os
os.environ['ERJI_CMC_KEY_FILE']='F:/key/CMC.txt'
from engine.repository import bootstrap,save_batch
from engine.pd2.providers import Supplemental,seed_verified_identity
from engine.adapters.expanded_markets import BinanceCashAdapter,alpha_quality
from engine.contracts import RuntimeConfig
from engine.adapters.base import public_error
async def main():
 bootstrap();seed_verified_identity();p=Supplemental()
 try:
  await p.cmc()
  from engine.pd2.storage import get
  print(json.dumps({'cmc_account_status':get('cmc','account','global')['status'],'btc_supply_status':get('cmc','valuation','1')['status']}))
 finally:await p.close()
 a=BinanceCashAdapter(alpha=True)
 try:
  b=await a.collect(RuntimeConfig(),history=False);save_batch(b)
  from engine.pd2.universe_policy import primary_eligible
  from types import SimpleNamespace
  print(json.dumps({'alpha_markets':len(b.instruments),'quality_eligible':sum(primary_eligible(SimpleNamespace(**i)) for i in b.instruments),'quotes':len(b.states),'errors':b.errors[:3]}))
 except Exception as e:print('alpha_probe',public_error(e))
 finally:await a.close()
asyncio.run(main())
