"""Synthetic local replay cost only. No database or exchange access."""
from pathlib import Path
import sys,json,time
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from engine.p37.detector import Detector

at=2_000_000_000_000
results=[]
for count in (5,10,20,50):
    d=Detector();ids=[f'binance:perp:BENCH{i}USDT' for i in range(650)]
    d.catalog={iid:dict(asset_id='BENCH'+str(n),venue='binance',symbol='BENCH'+str(n)+'USDT',liquidity=2_000_000,state={}) for n,iid in enumerate(ids)}
    d.remember_context(at-1800000)
    for n,iid in enumerate(ids):
        for stamp in range(at-1800000,at+1,1000):
            peak=at-120000+n*1000
            d.price(iid,stamp,'105' if n<count and stamp==peak else '100','mark')
    samples=[];events=[]
    for _ in range(3):
        start=time.perf_counter();events=d.evaluate_since({iid:at-300000 for iid in ids},at);samples.append(round((time.perf_counter()-start)*1000,2))
    recovered={iid for iid,f,_ in events if any(r['kind']=='price' and r['direction']=='up' for r in f['reasons'])}
    assert len(recovered)==count,(count,len(recovered))
    results.append({'markets':650,'temporary_peak_markets':count,'samples_ms':samples,'max_ms':max(samples),'recovered_peak_markets':len(recovered)})
out=root/'docs/p39-validation/timing-implementation-20261008/replay-benchmark.json'
out.write_text(json.dumps({'scope':'synthetic memory replay; 30 minutes of 1-second ticks, recent 5-minute scan; no HTTP, database writes, T2 or production timing','cases':results},indent=2),'utf8')
print(json.dumps(results))
