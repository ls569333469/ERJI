"""Offline provider fixture + one bounded read-only local sample; never activates."""
from pathlib import Path
import sys, json, time, socket, urllib.request, importlib.util
from datetime import datetime, timezone
from decimal import Decimal as D
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.p34_local import prepare
prepare()
from engine.db import Session
from engine.models import Instrument
from engine.pd2.analysis import structural
from engine.pd2.structural_history import load
from engine.pd2.config import AnalysisConfig
from engine.strategies.p33_lifecycle import freeze_plan
from sqlalchemy import text

OUT=ROOT/'docs/p39-validation/binance-three-optimizations'
fixture=json.loads((ROOT/'docs/p39-validation/binance-super-1d-history-verification.json').read_text('utf8'))
at=int(datetime.fromisoformat(fixture['checked_at']).timestamp()*1000)
rows=[dict(open_time=r[0],close_time=r[6]+1,received_at=at,is_closed=r[6]+1<=at,
    open=r[1],high=r[2],low=r[3],close=r[4],base_volume=r[5],quote_volume=r[7]) for r in fixture['raw']]
event=dict(market_id='binance:perp:SUPERUSDT',event_id='offline-super',
    entry_zone=['.28255262420933205490','.290766242093320549'],protection='.23069737579066794510',
    invalidation={},expires_at=at+86400000)
spec=importlib.util.spec_from_file_location('pre_optimization_plan',OUT/'before/engine/strategies/p33_lifecycle.py')
old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
legacy_start=int(datetime(2025,12,5,tzinfo=timezone.utc).timestamp()*1000)
legacy_rows=[b for b in rows if b['open_time']>=legacy_start]
old_plan=old.freeze_plan(event,{'1D':legacy_rows},at,'.00001')
start=time.perf_counter();context=structural(rows,'1D',at,'.00001',AnalysisConfig());cold_ms=(time.perf_counter()-start)*1000
new_plan=freeze_plan(event,{'1D':rows[-400:]},at,'.00001',AnalysisConfig(),{'1D':context})
result={'checked_at':datetime.now(timezone.utc).isoformat(),'exchange_requests':0,'monitor_resumed':False,
    'fixture_source_at':fixture['checked_at'],'fixture_is_current_quote':False,
    'super_offline':{'entry_upper':event['entry_zone'][1],'legacy_stored_start':legacy_start,'legacy_closed_bars':sum(b['is_closed'] for b in legacy_rows),'old_daily':old_plan['targets']['daily'],
        'new_daily':new_plan['targets']['daily'],'coverage':context['coverage'],'structural_cold_ms':round(cold_ms,2)},
    'local_read_only':{},'http':{},'ports':{},'runtime_activation':'not_requested'}
result['run_id']=json.loads((ROOT/'.runtime/processes.json').read_text('utf-8-sig'))[0]['run_id']
try:
    now=int(time.time()*1000)
    with Session() as s:
        s.execute(text('SET TRANSACTION READ ONLY'))
        s.execute(text("SET LOCAL statement_timeout='8s'"))
        inst=s.get(Instrument,'binance:perp:SUPERUSDT')
        t=time.perf_counter();first=load(s,inst,now,AnalysisConfig());ms=(time.perf_counter()-t)*1000
        t=time.perf_counter();again=load(s,inst,now,AnalysisConfig());cached_ms=(time.perf_counter()-t)*1000
        plan=freeze_plan(event,{},now,inst.spec['tick_size'],AnalysisConfig(),first)
        result['local_read_only']={'market_id':inst.id,'cold_ms':round(ms,2),'cached_ms':round(cached_ms,2),
            'measure_scope':'one SUPER local native history read; not queue or whole system benchmark',
            'coverage':{tf:v['coverage'] for tf,v in first.items()},
            'targets_for_historical_entry_reference_only':plan['targets']}
        assert first==again
except Exception as error:
    result['local_read_only']={'error':type(error).__name__,'detail':str(error).split('[SQL:')[0][:200]}
for port in (18770,18771,18772,55432):
    with socket.socket() as sock:
        sock.settimeout(1);result['ports'][str(port)]=sock.connect_ex(('127.0.0.1',port))==0
for url in ('http://127.0.0.1:18771/v1/health','http://127.0.0.1:18770/signals'):
    try:
        t=time.perf_counter()
        with urllib.request.urlopen(url,timeout=8) as response:
            result['http'][url]={'status':response.status,'ms':round((time.perf_counter()-t)*1000,2)}
    except Exception as error:result['http'][url]={'error':type(error).__name__}
(OUT/'acceptance-evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf8')
print(json.dumps({'super_offline_daily':result['super_offline']['new_daily'],
    'local_ms':{k:v for k,v in result['local_read_only'].items() if k.endswith('_ms')},
    'local_error':result['local_read_only'].get('error'),'ports':result['ports'],'http':result['http']},ensure_ascii=False,default=str))
