import asyncio
from copy import deepcopy
from decimal import Decimal as D
from hashlib import sha256
from io import BytesIO
import zipfile
from types import SimpleNamespace as N
import pytest
from sqlalchemy import select
from engine.models import Candle, RawEvent, Instrument, CurrentSignal, SignalSnapshot, Task
from engine.pd2.analysis import structural, technical, level_role
from engine.pd2.config import AnalysisConfig
from engine.pd2 import structural_history, history_backfill, p35_archive, service, signal_projection
from engine.p38 import jobs
from engine.strategies.p33_lifecycle import freeze_plan
from test_p25_signals import isolated
from test_p39_reliability import seed_plan

NOW=2000000000000//86400000*86400000
DAY=86400000


def history(count=600):
    return [dict(open_time=NOW-(count-i)*DAY,close_time=NOW-(count-i-1)*DAY,
        received_at=NOW,is_closed=True,open='10',high='15' if i==100 else '30' if i==550 else '11',
        low='9',close='10',base_volume='1',quote_volume='10',revision=1) for i in range(count)]


def hit():
    return dict(market_id='binance:perp:P39TESTUSDT',event_id='test-history',entry_zone=['10','11'],
        protection='9',invalidation={},expires_at=NOW+DAY)


def test_old_resistance_used_with_same_chart_plan_identity_and_config():
    rows=history();cfg=AnalysisConfig(pivot_left=3,pivot_right=4,zone_atr_multiplier=.4)
    short=freeze_plan(hit(),{'1D':rows[-400:]},NOW,'.01',cfg)
    context={'1D':structural(rows,'1D',NOW,'.01',cfg)}
    plan=freeze_plan(hit(),{'1D':rows[-400:]},NOW,'.01',cfg,context)
    chart=structural_history.merge(technical(rows[-200:],'1D',NOW,'.01',cfg),context['1D'])
    target=plan['targets']['daily']
    assert D(target['low'])<D(short['targets']['daily']['low'])
    same=next(v for v in chart['levels'] if v['id']==target['id'])
    assert same==target and target['anchor']['price']=='15'
    assert len(target['anchor']['source_bar_ids'])==8
    assert plan['target_coverage']['1D']['analyzed_closed_bars']==600
    # Historical bar acquired today cannot appear in yesterday's plan.
    assert freeze_plan(hit(),{'1D':rows},NOW-1,'.01',cfg)['targets']['t1'] is None
    broken=deepcopy(rows);broken[590].update(high='17',close='17');broken[591].update(high='17',low='15',close='17')
    for b in broken[592:]:b.update(open='17',close='17',high='18',low='16')
    live=structural(broken,'1D',NOW,'.01',cfg)
    old=next(v for v in live['levels'] if v['id']==target['id'])
    assert old['kind']=='support' and old['role_events'][0]['event']=='broken'
    assert freeze_plan(hit(),{},NOW,'.01',cfg,{'1D':live})['targets']['daily']['id']!=target['id']


def test_gap_stops_history_instead_of_bridging_unknown_candles():
    rows=history();del rows[200]
    assessment=structural(rows,'1D',NOW,'.01')
    assert assessment['coverage']['gap_count']==1
    assert assessment['coverage']['analyzed_closed_bars']==399
    assert not any(v['anchor']['price']=='15' for v in assessment['levels'])


def test_old_revision_invalidates_structure_cache_and_deep_version_not_fast(isolated):
    with isolated.begin() as s:
        iid,_=seed_plan(s,NOW);inst=s.get(Instrument,iid);inst.spec={'tick_size':'.01'}
        s.add(RawEvent(id='structural-fixture',source='test',endpoint='offline',received_at=NOW,payload={}));s.flush()
        for b in history():s.add(Candle(instrument_id=iid,resolution='1D',raw_ref='structural-fixture',**b))
    cfg=AnalysisConfig()
    with isolated() as s:first=structural_history.load(s,s.get(Instrument,iid),NOW,cfg)
    deep=jobs.dependency_versions([iid],NOW,'structure')[iid]
    fast=jobs.dependency_versions([iid],NOW,'entry')[iid]
    with isolated.begin() as s:
        c=s.get(Candle,(iid,'1D',history()[100]['open_time']));c.high=D('13');c.revision+=1
    with isolated() as s:second=structural_history.load(s,s.get(Instrument,iid),NOW,cfg)
    assert first['1D']['coverage']['revision']!=second['1D']['coverage']['revision']
    assert jobs.dependency_versions([iid],NOW,'structure')[iid]!=deep
    assert jobs.dependency_versions([iid],NOW,'entry')[iid]==fast
    assert D(next(v for v in second['1D']['levels'] if v['anchor']['pivot_time']==history()[100]['open_time'])['anchor']['price'])==D('13')


@pytest.mark.parametrize('micros',[False,True])
def test_daily_archive_units_checksum_and_received_provenance(micros):
    start=NOW-3*DAY;factor=1000 if micros else 1
    out=BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('data.csv',f'{start*factor},10,11,9,10,1,{(start+DAY)*factor-1},10\n')
    blob=out.getvalue();checksum=sha256(blob).hexdigest()
    rows,_=p35_archive.parse(blob,checksum,'test','1D',start,start+DAY,start+2*DAY)
    assert rows[0]['open_time']==start and rows[0]['close_time']==start+DAY
    assert rows[0]['received_at']==start+2*DAY and rows[0]['high']=='11'
    with pytest.raises(ValueError,match='checksum'):p35_archive.parse(blob,'bad','test','1D',start,start+DAY,start+2*DAY)


def test_daily_history_cursor_resumes_and_stops_at_first_missing_day(monkeypatch):
    from engine.p38 import candle_gaps
    from engine.adapters.base import Batch
    start=100*DAY;state=dict(timeframe='1D',status='progress',start=start,end=start+3*DAY,next_open=start+DAY)
    inst=N(id='test',native_symbol='TESTUSDT');saved=[];requests=[]
    monkeypatch.setattr(history_backfill,'target',lambda:(inst,state))
    monkeypatch.setattr(history_backfill,'checkpoint',lambda iid,value:saved.append(value))
    monkeypatch.setattr(history_backfill,'acknowledge',lambda *a:None)
    monkeypatch.setattr(history_backfill,'stored_advance',lambda iid,cursor,end,tf:cursor)
    async def fetch(adapter,target,lane,archive=None):
        requests.append((target,lane));batch=Batch('binance',candle_only=True)
        batch.candles=[dict(open_time=start+DAY,close_time=start+2*DAY,is_closed=True),
            dict(open_time=start+3*DAY,close_time=start+4*DAY,is_closed=True)]
        return batch
    monkeypatch.setattr(candle_gaps,'fetch',fetch)
    asyncio.run(history_backfill.collect_one(N()))
    assert requests[0][0][3]=='1d' and requests[0][0][5]==start+DAY and requests[0][1]=='history'
    assert saved[0]['next_open']==start+2*DAY
    assert history_backfill.state_key('test','240')=='p34_history:test'
    assert history_backfill.state_key('test','1D')=='p34_history:1D:test'


def test_history_shared_hit_advances_persisted_cursor_without_another_request(monkeypatch):
    from engine.p38 import candle_gaps
    start=100*DAY;state=dict(timeframe='1D',status='progress',start=start,end=start+3*DAY,next_open=start)
    saved=[]
    monkeypatch.setattr(history_backfill,'target',lambda:(N(id='test',native_symbol='TESTUSDT'),state))
    monkeypatch.setattr(history_backfill,'checkpoint',lambda iid,value:saved.append(value))
    monkeypatch.setattr(history_backfill,'stored_advance',lambda *args:start+2*DAY)
    async def shared(*args,**kw):return None
    monkeypatch.setattr(candle_gaps,'fetch',shared)
    assert asyncio.run(history_backfill.collect_one(N()))['status']=='shared_coverage_progress'
    assert saved[0]['next_open']==start+2*DAY


def test_fast_snapshot_publish_preserves_candidates_skips_auxiliary_and_expires(isolated,monkeypatch):
    with isolated.begin() as s:
        iid,snap=seed_plan(s,NOW)
        snap={**snap,'as_of':NOW,'market':{**snap['market'],'price':'10','as_of':NOW},
            'valid_until':NOW+10000,'execution':{'received_at':NOW}}
        s.get(SignalSnapshot,'p39-old').payload=snap;s.get(SignalSnapshot,'p39-old').as_of=NOW
        s.add(CurrentSignal(id='candidate-retained',asset_id='P39TEST',instrument_id=iid,action='waiting',
            first_seen_at=NOW,computed_at=NOW-1,valid_until=NOW+360000,payload={'id':'candidate'}))
    from engine.pd2 import derivatives
    monkeypatch.setattr(derivatives,'shared_inputs',lambda *a:pytest.fail('Fast publication must not reconstruct auxiliary facts'))
    monkeypatch.setattr(service,'now_ms',lambda:NOW)
    monkeypatch.setattr(signal_projection,'now_ms',lambda:NOW)
    service.decision_list(instrument_ids=[iid],fast=True)
    with isolated() as s:assert s.get(CurrentSignal,'candidate-retained')
    assert any(r['id']=='p39-e' for r in signal_projection.read('buy')['rows'])
    monkeypatch.setattr(signal_projection,'now_ms',lambda:NOW+10001)
    assert not signal_projection.read('buy')['rows']


def test_supersession_is_not_failure_but_actual_timeout_is(isolated):
    jobs.submit(['test'],at=NOW,work_kind='projection');lease=jobs.take(NOW,work_kinds=['projection'])
    jobs.finish(lease,'analysis_source_changed',NOW+1)
    with isolated() as s:
        row=s.get(Task,lease['id']);assert row.state=='queued' and row.payload['failed_count']==0
    lease=jobs.take(NOW+251,work_kinds=['projection']);jobs.finish(lease,'TimeoutError',NOW+252)
    with isolated() as s:
        row=s.get(Task,lease['id']);assert row.state=='failed' and row.payload['failed_count']==1
