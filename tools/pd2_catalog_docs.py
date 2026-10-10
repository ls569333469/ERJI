import httpx,ssl,json,re
from pathlib import Path
urls={'binance-skills':'https://raw.githubusercontent.com/binance/binance-skills-hub/master/README.md'}
with httpx.Client(verify=ssl.create_default_context(),timeout=20,follow_redirects=True) as c:
 for name,url in urls.items():
  try:
   r=c.get(url);r.raise_for_status();Path('docs/api/'+name+'.txt').write_text(r.text,encoding='utf8')
   found=re.findall(r'.{0,70}(?:api/liqmap|symbols|rising|meme-rush|crypto-market-rank).{0,170}',r.text)
   print(json.dumps({'name':name,'status':r.status_code,'matches':found[:12]},ensure_ascii=False))
  except Exception as e:print(json.dumps({'name':name,'error':type(e).__name__}))
