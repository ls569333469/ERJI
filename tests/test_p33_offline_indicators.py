from copy import deepcopy
from test_p25_signals import isolated
from test_p33_avwap_rebuild import seed,IID,ANCHOR,STEP
from engine.models import Candle,SnapshotDataset
from engine.pd2.offline_indicators import build,read,source_state,refresh_one
from engine.pd2 import indicator_history


def test_coverage_worker_runs_offline_history_job_once(monkeypatch):
    import asyncio
    from services.worker.main import run_coverage
    from engine.pd2 import signal_projection,offline_indicators
    calls=[]
    monkeypatch.setattr(signal_projection,'refresh_coverage',lambda:calls.append('coverage'))
    monkeypatch.setattr(offline_indicators,'refresh_one',lambda now:(calls.append(now) or {'status':'built'}))
    monkeypatch.setattr('services.worker.main.heartbeat',lambda *a:None)
    asyncio.run(run_coverage(once=True))
    assert calls[0]=='coverage' and len(calls)==2
    assert isinstance(calls[1],int) and calls[1]>0


def test_long_history_get_reads_only_prebuilt_pages_and_rejects_changed_source(isolated,monkeypatch):
    now=ANCHOR+21000*STEP
    with isolated.begin() as s:
        seed(s,20040)
        from types import SimpleNamespace
        from engine.strategies.p33_coverage import load as coverage
        contract=SimpleNamespace(id=IID,spec={'source':'exchangeInfo','onboard_time':ANCHOR})
        covered=coverage(s,contract,ANCHOR+20040*STEP)
        assert covered['complete'] and covered['bar_count']==20040 and float(covered['high'])==11
        missing=indicator_history.page(s,IID,'240',None,80,now)
        assert missing['status']=='offline_rebuild_required'
        result=refresh_one(now)
        assert result['status']=='built' and result['count']==20040
        monkeypatch.setattr(indicator_history,'calculate',lambda *args:(_ for _ in ()).throw(AssertionError('GET must not recalculate')))
        current=indicator_history.page(s,IID,'240',None,80,now)
        older=indicator_history.page(s,IID,'240',current['next_before'],80,now)
        assert current['status']=='available' and len(current['rows'])==len(older['rows'])==80
        assert older['series_revision']==current['series_revision']
        assert older['rows'][-1]['time']<current['rows'][0]['time']
        assert float(current['rows'][-1]['ema20'])==10
        candle=s.get(Candle,(IID,'240',ANCHOR+5*STEP));candle.high=15;candle.revision+=1;candle.received_at=now;s.flush()
        changed=indicator_history.page(s,IID,'240',None,80,now)
        assert changed['rows']==[] and changed['status']=='offline_rebuild_required'
        assert coverage(s,contract,ANCHOR+20040*STEP) is None  # corrected candle not yet knowable at the old cutoff
