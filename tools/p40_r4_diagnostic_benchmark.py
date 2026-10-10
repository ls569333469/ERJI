"""Finite R3/R4 CPU comparison; no DB or network connections."""
from pathlib import Path
import sys,time,json,statistics,importlib.util,gc
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
OUT=ROOT/'docs/p40-validation/r4-implementation-20261010'
def audit(event,args):
    if event=='socket.connect':raise RuntimeError('benchmark cannot connect to database or network')
sys.addaudithook(audit)
spec=importlib.util.spec_from_file_location('r4_before_detector',OUT/'before/engine/p37/detector.py')
old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
from engine.p37.detector import Detector
from engine.p37.worker import discovery_health_snapshot
catalog={f'binance:perp:X{n}USDT':{'venue':'binance','symbol':str(n),'liquidity':2000000} for n in range(750)}
trials={'r3':[],'r4':[]};last=None
for trial in range(3):
    for name,cls in ([('r3',old.Detector),('r4',Detector)] if trial%2==0 else [('r4',Detector),('r3',old.Detector)]):
        gc.collect();d=cls();d.catalog=catalog;started=time.perf_counter()
        for frame in range(960):
            at=1000000+frame*1000
            for iid in catalog:d.price(iid,at,'100','mark',at,source_kind='markPriceUpdate') if name=='r4' else d.price(iid,at,'100','mark',at)
        trials[name].append((time.perf_counter()-started)*1000/960)
        if name=='r4':last=d
capture=time.perf_counter();health=discovery_health_snapshot(last,lambda:at)
health_ms=(time.perf_counter()-capture)*1000
result={'scope':'750 markets, 960 one-second frames, three CPU-only trials; not production capacity',
    'frame_ms':trials,'median_r3_ms':round(statistics.median(trials['r3']),3),'median_r4_ms':round(statistics.median(trials['r4']),3),
    'health_snapshot_ms':round(health_ms,3),'receipt_examples':len(health['price_receipts']['examples']),
    'bounded_receipt_json_bytes':len(json.dumps(health['price_receipts']).encode('utf8')),
    'production_access':False,'exchange_calls':False,'tick_database_writes':0}
(OUT/'diagnostic-benchmark.json').write_text(json.dumps(result,indent=2),'utf8');print(json.dumps(result))
