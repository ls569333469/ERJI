import json,urllib.request,ssl
from pathlib import Path
url='https://api.github.com/repos/binance/binance-skills-hub/git/trees/HEAD?recursive=1'
try:
 with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'ERJI-research'}),context=ssl.create_default_context(),timeout=20) as r:data=json.load(r)
 Path('docs/api/pd2-binance-skills-tree.json').write_text(json.dumps(data,ensure_ascii=False),encoding='utf8')
 print(json.dumps([v['path'] for v in data.get('tree',[]) if any(k in v['path'].lower() for k in ['meme','rank','market','token'])]))
except Exception as e: print(type(e).__name__,getattr(e,'code',None))
