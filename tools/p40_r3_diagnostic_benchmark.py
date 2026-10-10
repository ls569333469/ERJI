"""Finite CPU replay, no database connections, network calls or synthetic live facts."""
from pathlib import Path
import sys,time,json,statistics,importlib.util,gc
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
OUT=ROOT/'docs/p40-validation/r3-implementation-20261009'
def audit(event,args):
    if event=='socket.connect':raise RuntimeError('benchmark cannot connect to database or network')
sys.addaudithook(audit)
spec=importlib.util.spec_from_file_location('r3_before_detector',OUT/'before/engine/p37/detector.py')
old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
from engine.p37.detector import Detector
from engine.p37.worker import discovery_health_snapshot
catalog={f'binance:perp:X{n}USDT':{'venue':'binance','symbol':str(n),'liquidity':2000000} for n in range(750)}
trials={'before':[],'r3':[]};last=None
for trial in range(3):
    for name,cls in ([('before',old.Detector),('r3',Detector)] if trial%2==0 else [('r3',Detector),('before',old.Detector)]):
        gc.collect()
        d=cls();d.catalog=catalog;started=time.perf_counter()
        for frame in range(960):
            at=1000000+frame*1000
            for iid in catalog:d.price(iid,at,'100','mark',at)
        trials[name].append((time.perf_counter()-started)*1000/960)
        if name=='r3':last=d
capture=time.perf_counter();health=discovery_health_snapshot(last,lambda:at)
health_ms=(time.perf_counter()-capture)*1000
size=len(json.dumps(health['price_receipts'],ensure_ascii=False).encode('utf8'))
result={'scope':'750 native markets, 960 one-second frames, three finite CPU-only trials; not production capacity',
    'frame_ms':trials,'median_before_ms':round(statistics.median(trials['before']),3),
    'median_r3_ms':round(statistics.median(trials['r3']),3),'health_snapshot_ms':round(health_ms,3),
    'bounded_receipt_json_bytes':size,'receipt_examples':len(health['price_receipts']['examples']),
    'production_access':False,'exchange_calls':False,'tick_database_writes':0}
(OUT/'diagnostic-benchmark.json').write_text(json.dumps(result,indent=2),'utf8')
print(json.dumps(result))
