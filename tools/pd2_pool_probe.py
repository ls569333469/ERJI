import httpx,json,ssl
from pathlib import Path
row=json.loads(Path('docs/api/probe-rising.json').read_text())['data']['tokens'][0]
c=httpx.Client(timeout=25,verify=ssl.create_default_context())
u='https://api.geckoterminal.com/api/v2/networks/bsc/tokens/'+row['contractAddress']+'/pools'
r=c.get(u);print(r.status_code)
if r.status_code==200:
 data=r.json();Path('docs/api/probe-pools.json').write_text(json.dumps(data));print(json.dumps(data.get('data',[])[:1]))
 if data.get('data'):
  a=data['data'][0]['attributes']['address'];r=c.get('https://api.geckoterminal.com/api/v2/networks/bsc/pools/'+a+'/ohlcv/hour',params={'aggregate':4,'currency':'usd','token':'base','limit':100});print(r.status_code);Path('docs/api/probe-pool-ohlcv.json').write_text(r.text);print(r.text[:1200])
