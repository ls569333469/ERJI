"""Read-only topology/endpoint inventory; no DB connection or provider HTTP."""
from pathlib import Path
import sys,json,socket,hashlib,subprocess
from datetime import datetime,timezone,timedelta
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
import yaml
from engine.pd2.p36_budget import specification,LIMITS
from engine.pd2.request_budget import budget_spec
doc=yaml.safe_load((root/'compose.yml').read_text(encoding='utf8'))
services=doc['services'];checks=[]
def check(name,value):
    assert value,name
    checks.append(name)
owner=services['worker-budget']
check('one private budget role',sum('--role' in s.get('command',[]) and 'budget' in s.get('command',[]) for s in services.values())==1 and not owner.get('ports'))
check('budget reachable privately',owner['environment']['ERJI_BUDGET_BIND']=='0.0.0.0')
for name in ('api','worker-live','worker-jobs','worker-streams','worker-notify'):
    env=services[name]['environment']
    check(name+' uses shared owner and read-only secret mount',env['ERJI_BUDGET_HOST']=='worker-budget' and env['ERJI_BUDGET_TOKEN_FILE']=='/run/secrets/budget-owner.token' and any(v.endswith('/run/secrets:ro') for v in services[name]['volumes']))
check('owner requires successful migration',owner['depends_on']['migrate']['condition']=='service_completed_successfully')
check('API requires healthy owner',services['api']['depends_on']['worker-budget']['condition']=='service_healthy')
for name in ('worker-live','worker-jobs','worker-streams','worker-notify','web'):
    check(name+' requires healthy API',services[name]['depends_on']['api']['condition']=='service_healthy')
rows=[]
um='https://fapi.binance.com/fapi/v1/';spot='https://data-api.binance.vision/api/v3/'
samples=[(um+k,p) for k,p in [('openInterest',{}),('exchangeInfo',{}),('ticker/24hr',{}),('premiumIndex',{}),('depth',{'limit':100}),('klines',{'limit':300}),('fundingRate',{}),('fundingInfo',{})]]
samples+=[('https://fapi.binance.com/futures/data/openInterestHist',{}),(spot+'exchangeInfo',{}),(spot+'ticker/24hr',{}),(spot+'depth',{'limit':100}),(spot+'klines',{'limit':300}),('https://data.binance.vision/data/test.zip',{})]
for url,params in samples:
    spec=specification(url,{'params':params});check('registered '+url,spec is not None)
    rows.append({'url':url,'params':params,'family':spec[0],'cost':spec[1],'registry':'binance-public-20261004-v1'})
legacy=[]
for url in ('https://www.binance.com/bapi/defi/v1/public/alpha-trade/fullDepth','https://fapi.asterdex.com/fapi/v3/depth','https://www.okx.com/api/v5/market/books','https://api.hyperliquid.xyz/info','https://pro-api.coinmarketcap.com/v1/cryptocurrency/map','https://bykaranteli.com/liqmap','https://api.geckoterminal.com/api/v2/networks/test'):
    check('unverified source does not borrow UM quota '+url,specification(url,{}) is None)
    host,weight,cap,gap=budget_spec(url,{})
    legacy.append({'url':url,'host':host,'legacy_weight':weight,'legacy_capacity_per_minute':cap,'minimum_gap_ms':gap,'status':'conservative_unverified; no official 90 percent claim or live API check'})
ports=[]
for port in (18770,18771,18772,55432):
    with socket.socket() as s:
        s.settimeout(.3);ports.append({'port':port,'listening':s.connect_ex(('127.0.0.1',port))==0})
files=('engine/repository.py','engine/db.py','engine/p38/jobs.py','engine/p38/p33_current.py','engine/p38/lifecycle.py','engine/pd2/native_streams.py','engine/pd2/write_queue.py','engine/pd2/book_clock.py','engine/pd2/outcomes.py','engine/pd2/notifications.py','engine/strategies/p33_lifecycle.py','apps/web/src/components/opportunity-board.tsx','apps/web/src/components/opportunity-detail.tsx','apps/web/src/components/observation-board.tsx','apps/web/src/components/chart.tsx','compose.yml')
report={'checked_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),'status':'passed','checks':checks,'verified_budget_limits':LIMITS,'registered_endpoints':rows,'legacy_endpoint_boundaries':legacy,'production_ports':ports,'production_db_access':False,'exchange_requests':0,'container_startup_verified':False,'source_hashes':{p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in files},'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()}
(root/'docs/p39-validation/static-review.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'static_checks':len(checks),'modern_endpoints':len(rows),'legacy_endpoints':len(legacy),'ports':ports},ensure_ascii=False))
