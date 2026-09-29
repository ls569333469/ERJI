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
from engine.pd2.scheduling import rotation
from engine.pd2.storage import get
try:print(rotation('hyperliquid',['BTC','ETH'],2))
except Exception as e:print(type(e).__name__,str(getattr(e,'orig',''))[:400])
for cap in ['symbols','whales','markets','liqmap','liquidations']:
 d=get('bykaranteli',cap,'BTC' if cap=='liqmap' else 'global').get('payload') or {}
 if cap=='symbols':print(json.dumps({'cap':cap,'total':d.get('totalSymbols'),'sample':d.get('symbols',[])[:2]}))
 elif cap=='whales':print(json.dumps({'cap':cap,'position':(d.get('positions') or [])[:1]}))
 elif cap=='markets':print(json.dumps({'cap':cap,'row':d.get('rows',[])[:1],'symbol':d.get('symbols',[])[:1]}))
 elif cap=='liqmap':print(json.dumps({'cap':cap,'as_of':d.get('as_of'),'meta':d.get('meta')}))
 else:print(json.dumps({'cap':cap,'recorded':d.get('recorded'),'sample':d.get('by_symbol',[])[:1]}))
