from copy import deepcopy
from types import SimpleNamespace as N
from decimal import Decimal as D
from io import BytesIO
import zipfile,hashlib
import pytest
from sqlalchemy import select,func
from engine.contracts import RuntimeConfig
from engine.models import Asset,Instrument,Episode,Campaign,RawEvent,Plan,SignalSnapshot
from engine.strategies.p35_candidates import sync_candidates,candidate_drawing
from engine.strategies.p33_delivery import make_snapshot,publish
from engine.pd2.p35_archive import parse
from engine.pd2.request_budget import budget_spec,budget_key
from test_p33_completion import event
from test_p33_patterns import NOW
from test_p25_signals import isolated


def instrument(session):
    asset='P35CHECK';iid='binance:perp:P35CHECKUSDT'
    session.add(Asset(id=asset,symbol=asset,known_at=NOW));session.flush()
    inst=Instrument(id=iid,asset_id=asset,venue='binance',native_symbol=asset+'USDT',market_type='linear_perpetual',quote_asset='USDT',spec={'tick_size':'.01'})
    session.add(inst);session.flush();return inst


def test_candidate_persists_first_record_and_disabled_never_revives(isolated):
    e,bars=event();hit=e['initial'];hit.update(status='waiting');hit.pop('trigger',None)
    hit['anchors']=[a for a in hit['anchors'] if a['kind']!='trigger_bar']
    cfg=RuntimeConfig()
    with isolated.begin() as s:
        inst=instrument(s);hit['market_id']=inst.id
        result={'results':[hit]};market={'price':hit['threshold']}
        sync_candidates(s,inst,result,bars,market,NOW,cfg);s.flush()
        row=s.scalar(select(Episode).join(Campaign).where(Campaign.asset_id==inst.asset_id));assert row and not row.evidence.get('trigger')
        first=deepcopy(s.get(RawEvent,row.id).payload)
        sync_candidates(s,inst,result,bars,market,NOW+1000,cfg);s.flush()
        assert s.scalar(select(func.count()).select_from(Episode).join(Campaign).where(Campaign.asset_id==inst.asset_id))==1
        assert s.get(RawEvent,row.id).payload==first
        assert candidate_drawing(row.id,inst.id,row.evidence,row.phase,NOW+1000)['status']=='candidate'
        bad={**row.evidence,'structure_valid_until':NOW}
        assert candidate_drawing(row.id,inst.id,bad,row.phase,NOW+1) is None
        cfg.disabled_strategies=[hit['strategy_id']]
        sync_candidates(s,inst,result,bars,market,NOW+2000,cfg);s.flush();assert row.phase=='ended'
        cfg.disabled_strategies=[]
        sync_candidates(s,inst,result,bars,market,NOW+3000,cfg);s.flush();assert row.phase=='ended'


def test_no_report_does_not_remove_data_pause_or_revive_expired_event(isolated):
    e,bars=event();cfg=RuntimeConfig()
    with isolated.begin() as s:
        inst=instrument(s);e['initial']['market_id']=inst.id
        e['qualification']={'rule_action':{'code':'paused','label':'等待数据','reason':'盘口缺失'},'execution':{'status':'unavailable'}}
        snap=make_snapshot(e,bars,{'instrument_id':inst.id,'asset_id':inst.asset_id,'price':'12'},'.01',NOW)
        assert publish(s,snap,e,inst,cfg,NOW,'p35-group')['enabled'];s.flush()
        stored=s.scalar(select(SignalSnapshot).join(Plan).where(Plan.episode_id==e['initial']['event_id'])).payload
        assert stored['action']['code']=='paused' and stored['drawing_contract']['entry_zone'] is None
        assert stored['validation_status']=='unvalidated'
        e['initial']['event_id']='p35-expired';snap['event_id']='p35-expired';e['plan']['expires_at']=NOW
        assert not publish(s,snap,e,inst,cfg,NOW,'p35-expired-group')['enabled']
        cfg.disabled_strategies=[snap['strategy_id']]
        assert not publish(s,snap,e,inst,cfg,NOW,'p35-group')['enabled']


def test_archive_checksum_and_continuity_not_fabricated():
    buffer=BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:z.writestr('bars.csv','open_time,open,high,low,close,volume,close_time,quote_volume\n14400000,10,12,9,11,5,28799999,55\n43200000,11,13,10,12,5,57599999,60\n')
    blob=buffer.getvalue();digest=hashlib.sha256(blob).hexdigest()
    rows,ref=parse(blob,digest,'binance:perp:TESTUSDT','240',14400000,60000000,60000000)
    from engine.pd2.history_backfill import advance
    assert len(rows)==2 and advance(14400000,60000000,rows)==28800000
    assert all(r['received_at']==60000000 for r in rows)
    with pytest.raises(ValueError):parse(blob,'0'*64,'test','240',0,60000000,60000000)


def test_budget_weights_shared_cap_and_disabled_strategy_validation():
    assert budget_key('data.binance.vision')==budget_key('fapi.binance.com')
    for size,expected in [(2,1),(300,2),(1000,5),(1500,10)]:
        _,weight,cap,_=budget_spec('https://fapi.binance.com/fapi/v1/klines',{'params':{'limit':size}})
        assert (weight,cap)==(expected,2160)
    with pytest.raises(ValueError):RuntimeConfig(disabled_strategies=['invented_strategy'])


def test_stable_binance_reference_does_not_chase_fresh_auxiliary():
    from engine.pd2.derivatives import choose_reference
    rows=[{'instrument_id':'binance:perp:XUSDT','venue':'binance','quote_asset':'USDT','fresh_oi':False},
          {'instrument_id':'binance:perp:XUSDC','venue':'binance','quote_asset':'USDC','fresh_oi':True},
          {'instrument_id':'aster:perp:XUSDT','venue':'aster','quote_asset':'USDT','fresh_oi':True}]
    assert choose_reference(rows,None)['instrument_id']=='binance:perp:XUSDT'


def test_all_ten_templates_have_individual_records():
    from engine.strategies.p35_templates import catalog,document
    rows=catalog();assert len(rows)==10
    assert all(document(r['id']) and r['dependencies'] for r in rows)
    assert document('not_a_strategy') is None


def test_background_cache_is_json_stable_and_not_a_weekly_veto(isolated):
    from engine.pd2.p35_schedule import background,windows
    e,bars=event();cfg=RuntimeConfig()
    with isolated.begin() as session:
        inst=instrument(session)
        first=background(session,inst,bars,NOW,cfg.analysis);session.flush()
        again=background(session,inst,bars,NOW+1000,cfg.analysis)
        assert again['as_of']==first['as_of'] and again['veto'] is False
        assert windows(cfg)['240']>=273


def test_legacy_cold_lane_respects_unchanged_cap_and_priority(isolated,monkeypatch):
    from engine.pd2 import request_budget as b
    from engine.models import UserPreference
    monkeypatch.setattr(b,'now_ms',lambda:NOW)
    monkeypatch.setattr(b,'budget_key',lambda host:'p35_test_only_budget')
    monkeypatch.setattr(b,'budget_spec',lambda *a:('legacy.test',2,300,500))
    url='https://fapi.binance.com/fapi/v1/klines'
    with isolated.begin() as s:
        s.merge(UserPreference(key='http_budget:p35_test_only_budget',updated_at=NOW,payload={'entries':[[NOW-1000,45,'cold']],'pending':[]}))
    token=b.request_lane.set('cold')
    try:assert b.reserve(url,{'params':{'limit':300}},'test-cold')>0
    finally:b.request_lane.reset(token)
    assert b.reserve('https://fapi.binance.com/fapi/v1/openInterest',{},'test-live')==0


def test_personal_sizing_does_not_hide_global_technical_action(isolated,monkeypatch):
    import engine.pd2.portfolio as portfolio
    monkeypatch.setattr(portfolio,'plan_risk',lambda *args:{'status':'risk_exhausted'})
    e,bars=event();cfg=RuntimeConfig()
    with isolated.begin() as s:
        inst=instrument(s);e['initial']['market_id']=inst.id
        e['qualification']={'rule_action':{'code':'participate','label':'可参与','reason':'实际条件满足'},'execution':{'status':'eligible','received_at':NOW}}
        snap=make_snapshot(e,bars,{'instrument_id':inst.id,'asset_id':inst.asset_id,'price':'12'},'.01',NOW)
        publish(s,snap,e,inst,cfg,NOW,'p35-sizing');s.flush()
        row=s.scalar(select(SignalSnapshot).join(Plan).where(Plan.episode_id==e['initial']['event_id']))
        assert row.payload['action']['code']=='participate' and row.payload['position_sizing']['status']=='risk_exhausted'


def test_scan_defers_global_event_lock_until_read_work_is_done(isolated):
    from engine.repository import emit
    from engine.models import StreamEvent
    with isolated.begin() as s:
        before=s.scalar(select(func.count()).select_from(StreamEvent))
        s.info['deferred_signal_events']=[]
        emit(s,'signal_update',{'asset_id':'P35CHECK'})
        assert s.scalar(select(func.count()).select_from(StreamEvent))==before
        # A separate reader is still usable before the event-ID lock is taken.
        with isolated() as reader:assert reader.scalar(select(func.count()).select_from(Asset))>=0
        for payload in s.info.pop('deferred_signal_events'):emit(s,'signal_update',payload)
        assert s.scalar(select(func.count()).select_from(StreamEvent))==before
    with isolated() as s:assert s.scalar(select(func.count()).select_from(StreamEvent))==before+1


def test_close_time_burst_keeps_bounded_transactions(monkeypatch):
    from engine import signal_service
    calls=[]
    def scan(ids):
        calls.append(ids)
        return {'scanned':len(ids),'matched':1}
    monkeypatch.setattr(signal_service,'_scan_signals',scan)
    result=signal_service.scan_signals(list(range(63)))
    assert [len(v) for v in calls]==[1]*63
    assert result=={'scanned':63,'matched':63}
