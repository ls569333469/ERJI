"""One bounded pre-activation snapshot. Local APIs/DB only, no business writes."""
import json,sys,time,socket,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.p34_local import prepare
prepare()
from sqlalchemy import create_engine,select,text
from sqlalchemy.orm import sessionmaker
from engine.config import settings
from engine.models import AnalysisDemand,WorkerHeartbeat
OUT=ROOT/'docs/p40-validation/implementation-20261009'
OUT.mkdir(parents=True,exist_ok=True)
at=int(time.time()*1000)
result={'as_of':at,'read_only':True,'stage':'before_activation','ports':{},'blind_spots':[],
    'registry':json.loads((ROOT/'.runtime/processes.json').read_text('utf-8-sig'))}
for port in (18770,18771,18772,55432):
    with socket.socket() as s:
        s.settimeout(2);result['ports'][str(port)]=s.connect_ex(('127.0.0.1',port))==0
for endpoint in ('health','operations'):
    try:
        with urllib.request.urlopen('http://127.0.0.1:18771/v1/'+endpoint,timeout=8) as response:
            result[endpoint]=json.load(response)
    except Exception as error:result['blind_spots'].append({'endpoint':endpoint,'error':type(error).__name__})
engine=create_engine(settings().database_url,connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=5000','connect_timeout':5},pool_size=1,max_overflow=0)
factory=sessionmaker(bind=engine,expire_on_commit=False)
try:
    with factory() as s:
        assert s.scalar(text('SHOW transaction_read_only'))=='on'
        result['database_read_only']=True
        demand=s.scalar(select(AnalysisDemand).where(AnalysisDemand.origin_type=='observation').order_by(AnalysisDemand.updated_at.desc()).limit(1))
        iid=demand.instrument_id if demand else None
        beat=s.get(WorkerHeartbeat,'anomaly_discovery')
        result['stored_discovery']={'state':beat.state,'updated_at':beat.updated_at,'details':beat.details} if beat else None
    if iid:
        from engine.p39 import diagnostics
        diagnostics.Session=factory
        result['native_event_trace']=diagnostics.trace(iid,5)
except Exception as error:result['blind_spots'].append({'scope':'read-only DB trace','error':type(error).__name__})
finally:engine.dispose()
(OUT/'production-before-activation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),'utf8')
print(json.dumps({'ports':result['ports'],'trace_instrument':result.get('native_event_trace',{}).get('instrument_id'),'blind_spots':result['blind_spots']},ensure_ascii=False))
