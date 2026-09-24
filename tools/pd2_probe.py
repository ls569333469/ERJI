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
from engine.repository import bootstrap
bootstrap()
from engine.pd2.service import analyze_market,refresh_plans,decision_list
a=analyze_market("binance:perp:BTCUSDT",series=False)
print(json.dumps({"analysis": [(x["timeframe"],x["status"],x["bar_count"]) for x in a["timeframes"]]}))
print(json.dumps(refresh_plans()))
print(json.dumps({"decisions":len(decision_list()["rows"])}))
from engine.pd2.providers import Supplemental,seed_verified_identity
seed_verified_identity()
async def run():
 a=Supplemental()
 try:
  result=await a.request("cmc","quote_probe","1","/v2/cryptocurrency/quotes/latest",{"id":"1","convert":"USD"},refresh=0)
  print(json.dumps({"provider":"cmc","status":result["status"],"fields":list((result.get("payload") or {}).keys())}))
  result=await a.request("bykaranteli","coverage","global","/api/public/coverage",refresh=0)
  print(json.dumps({"provider":"bykaranteli","status":result["status"],"fields":list((result.get("payload") or {}).keys())}))
 finally:await a.close()
asyncio.run(run())