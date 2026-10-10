import asyncio,json,ssl
from pathlib import Path
import httpx,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine.pd2.source_policy import assert_market_request
async def main():
 async with httpx.AsyncClient(timeout=30,verify=ssl.create_default_context(),follow_redirects=False) as c:
  specs=[('spot','https://api.binance.com/api/v3/exchangeInfo',None),('alpha','https://www.binance.com/bapi/defi/v1/public/alpha-trade/get-exchange-info',None),('alpha_tokens','https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list',None),('rising','https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/pulse/unified/rank/list/ai',{'rankType':10,'tabId':100,'memeTrending':True,'chainId':'56','period':50,'page':1,'size':20,'sortOrder':'default','marketCapMin':1000000})]
  for name,url,body in specs:
   try:
    assert_market_request(url)
    r=await c.post(url,json=body) if body else await c.get(url)
    r.raise_for_status();data=r.json();Path('docs/api/probe-'+name+'.json').write_text(json.dumps(data),encoding='utf8')
    rows=data.get('symbols') or data.get('data')
    if isinstance(rows,dict):rows=rows.get('symbols') or rows.get('tokens') or rows
    print(json.dumps({'source':name,'status':r.status_code,'count':len(rows) if rows else 0,'sample':rows[0] if isinstance(rows,list) and rows else rows},ensure_ascii=False),flush=True)
   except Exception as e:print(name,type(e).__name__,flush=True)
asyncio.run(main())
