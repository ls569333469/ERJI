from copy import deepcopy
import json
from pathlib import Path
from decimal import Decimal as D
from engine.pd2.persistent_avwap import accumulate
from engine.pd2.event_overlays import event_avwap
from engine.pd2.indicator_history import calculate, page
from engine.pd2.config import ValidationConfig
from engine.strategies.p33 import evaluate, VERSION, CONFIG_HASH
from engine.strategies.p33_lifecycle import freeze_plan, CONFIG_HASH as PLAN_HASH
from engine.strategies.p33_replay import CONFIG_HASH as COST_HASH
from engine.strategies.p33_delivery import make_snapshot, publish
from engine.strategies.p33_cases import match_cases
from engine.strategies.p33_release import assess, CHECKS
from engine.pd2.chart_contract import drawing_contract
from test_p33_patterns import base, bars, NOW, H
from test_p25_signals import isolated


def event():
    data=base();hit=evaluate(data,NOW,'.01','test')['main']
    return {'initial':hit,'plan':freeze_plan(hit,data,NOW,'.01'),'first_observed_at':NOW,'status':'triggered'},data


def test_accumulation_survives_cache_rolloff_and_matches_single_pass():
    rows=bars([10+i*.01 for i in range(600)])
    state,first=accumulate(None,rows[:300],rows[0]['open_time'],'240',NOW)
    resumed,last=accumulate(state,rows[280:],rows[0]['open_time'],'240',NOW)
    full=event_avwap(rows,rows[0]['open_time'],'240',NOW)
    assert last[-1]['event_avwap']==full['series'][-1]['event_avwap']
    assert len(first)+len(last)==600
    duplicate,points=accumulate(resumed,rows[-100:],rows[0]['open_time'],'240',NOW)
    assert points==[] and duplicate['weighted']==resumed['weighted']
    rows[-1]['base_volume']='201'
    conflict,points=accumulate(resumed,rows[-100:],rows[0]['open_time'],'240',NOW)
    assert conflict['status']=='revision_conflict' and points==[]


def test_accumulator_gap_is_not_reanchored_and_can_resume_after_backfill():
    rows=bars([10]*60);anchor=rows[0]['open_time']
    state,points=accumulate(None,rows[:20]+rows[21:],anchor,'240',NOW)
    assert state['status']=='interval_gap' and state['next_open']==rows[20]['open_time']
    fixed,rest=accumulate(state,rows[20:],anchor,'240',NOW)
    assert fixed['status']=='available' and len(rest)==40
    rows[21]['base_volume']=None
    failed,_=accumulate(state,rows[20:],anchor,'240',NOW)
    assert failed['status']=='missing_volume'
    rows[21]['base_volume']='100'
    recovered,_=accumulate(failed,rows[20:],anchor,'240',NOW)
    assert recovered['status']=='available' and recovered['next_open']==NOW


def test_indicator_pages_preserve_frozen_values_without_live_session():
    source=calculate(json.dumps(bars([10+i*.1 for i in range(100)])),'240')
    snap={'id':'fixed','as_of':NOW,'series_revision':'frozen','timeframe_assessments':[{'timeframe':'240','series':source}]}
    latest=page(None,'test','240',None,20,NOW,snap)
    older=page(None,'test','240',latest['next_before'],20,NOW,snap)
    assert latest['rows']==source[-20:] and older['rows']==source[-40:-20]
    assert older['series_revision']=='frozen' and older['snapshot_id']=='fixed'
    assert page(None,'test','240',source[0]['time'],20,NOW,snap)['rows']==[]


def test_indicator_gap_restarts_warmup_and_never_uses_future():
    rows=bars([10+i*.1 for i in range(100)]);rows.pop(70)
    result=calculate(json.dumps(rows),'240')
    assert result[70]['ema20'] is None and result[70]['macd'] is None


def test_research_snapshot_has_own_anchors_but_no_tradable_lines():
    e,data=event();snap=make_snapshot(e,data,None,'.01',NOW)
    assert snap['drawing_contract']['status']=='confirmed'
    assert snap['drawing_contract']['entry_zone'] is None
    assert all(a['kind']!='box' for a in snap['drawing_contract']['anchors'])
    assert snap['action']['code']=='paused' and snap['trading_enabled'] is False


def test_nonbox_released_contract_shares_plan_lines_and_history():
    e,data=event();snap=make_snapshot(e,data,None,'.01',NOW)
    snap.update(trading_enabled=True,validation_status='validated_version',action={'code':'participate'},valid_until=NOW+1000)
    actual=drawing_contract(snap)
    assert actual['entry_zone']['low']==e['initial']['entry_zone'][0]
    assert actual['protection']['price']==e['initial']['protection']
    assert actual['entry_zone']['valid_from']>=e['first_observed_at']
    snap['price_pattern']['market_id']='other'
    assert drawing_contract(snap)['status']=='missing_anchors'


def test_case_matching_never_adds_future_archive_to_frozen_snapshot(tmp_path):
    e,data=event();hit=e['initial'];old=deepcopy(hit);old.update(market_id='other',event_id='old')
    p=tmp_path/'cases.json';p.write_text(json.dumps({'cases':[{'id':'old','pattern':old,'symbol':'NOM','available_at':NOW+1,'source_sha256':{},'data_scope':'research'}]}))
    assert match_cases(hit,NOW,p)['matches']==[]
    actual=match_cases(hit,NOW+1,p)
    assert actual['matches'][0]['symbol']=='NOM' and actual['similarity_pct'] is None
    old['strategy_version']='other';p.write_text(json.dumps({'cases':[{'id':'old','pattern':old,'symbol':'NOM','available_at':NOW,'source_sha256':{},'data_scope':'research'}]}))
    assert match_cases(hit,NOW+1,p)['matches']==[]


def approved_report():
    return {'id':'test-only','strategy_id':'recent_high','rule_version':VERSION,'config_hash':CONFIG_HASH,'plan_hash':PLAN_HASH,'cost_hash':COST_HASH,
            'approved_at':NOW-1,'expires_at':NOW+1000,'reviewer':'test','protocol_registered_at':NOW-3,'evaluation_started_at':NOW-2,
            'sample':{'mature_fills':200,'assets':30,'days':90},'checks':{k:{'passed':True,'artifact_sha256':'test-evidence'} for k in CHECKS}}


def test_release_requires_every_frozen_protocol_gate():
    cfg=ValidationConfig();report=approved_report()
    assert assess(report,cfg,NOW,'recent_high')['enabled']
    for key in CHECKS:
        changed=deepcopy(report);changed['checks'][key]['passed']=False
        assert not assess(changed,cfg,NOW,'recent_high')['enabled']
    for changed in ({**report,'config_hash':'old'},{**report,'sample':{}},{**report,'protocol_registered_at':NOW},{**report,'approved_at':None}):
        assert not assess(changed,cfg,NOW,'recent_high')['enabled']


def test_no_report_cannot_publish_any_plan(isolated):
    from engine.repository import runtime_config
    from engine.models import Plan
    from sqlalchemy import select,func
    e,data=event();snap=make_snapshot(e,data,None,'.01',NOW)
    with isolated.begin() as session:
        before=session.scalar(select(func.count()).select_from(Plan))
        result=publish(session,snap,e,None,runtime_config(),NOW,'group')
        assert not result['enabled']
        assert session.scalar(select(func.count()).select_from(Plan))==before


def test_contract_lifetime_high_needs_listing_start_and_continuous_known_history():
    from engine.strategies.p33_coverage import proof
    rows=bars([10]*60);listed=rows[0]['open_time']+1000
    actual=proof(rows,'test',listed,NOW)
    assert actual['scope']=='contract_since_listing' and actual['high']=='10.3'
    assert proof(rows[1:],'test',listed,NOW) is None
    assert proof(rows[:10]+rows[11:],'test',listed,NOW) is None
    rows[-1]['received_at']=NOW+1
    assert proof(rows,'test',listed,NOW) is None


def test_publication_without_review_report_is_idempotent(isolated,monkeypatch):
    import engine.strategies.p33_delivery as delivery
    from engine.repository import runtime_config
    from engine.models import Plan,Asset,Instrument,SignalSnapshot
    from sqlalchemy import select,func
    e,data=event();iid=e['initial']['market_id']
    e['origin']={'origin_type':'observation','origin_observation_ids':['real-test-observation'],
        'analysis_result_id':'real-test-result','analysis_market_id':iid,'input_version':'fixed-native-version'}
    e['qualification']={'rule_action':{'code':'participate','label':'条件确认','reason':'test'},'execution':{'status':'eligible','received_at':NOW}}
    snap=make_snapshot(e,data,{'instrument_id':iid,'asset_id':'P33PUBLISH','venue':'binance','symbol':'P33PUBLISH','quote_asset':'USDT','price':'12'},'.01',NOW)
    with isolated.begin() as session:
        session.add(Asset(id='P33PUBLISH',symbol='P33PUBLISH',known_at=NOW));session.flush()
        instrument=Instrument(id=iid,asset_id='P33PUBLISH',venue='binance',native_symbol='P33PUBLISHUSDT',market_type='linear_perpetual',quote_asset='USDT',spec={})
        session.add(instrument);session.flush()
        for _ in range(2):publish(session,snap,e,instrument,runtime_config(),NOW,'p33-test-group');session.flush()
        plan=session.get(Plan,e['initial']['event_id']+':p33')
        assert {'config','evidence','original_t1','original_protection'}<=set(plan.payload)
        assert plan.payload['origin']==e['origin']
        assert session.scalar(select(func.count()).select_from(SignalSnapshot).where(SignalSnapshot.plan_id==plan.id))==1
        stored=session.scalar(select(SignalSnapshot).where(SignalSnapshot.plan_id==plan.id)).payload
        assert stored['drawing_contract']['entry_zone'] and not stored['trading_enabled']
        assert stored['origin']==e['origin'] and stored['evidence']['origin']==e['origin']
        from engine.pd2.signal_projection import compact
        assert compact(stored)['origin']==e['origin']
        assert stored['action']['code']=='participate' and stored['validation_status']=='unvalidated'


def test_collection_repairs_old_unfinalized_row_instead_of_only_latest(isolated,monkeypatch):
    from engine import repository
    from engine.pd2 import collection_schedule
    from engine.models import Asset,Instrument,Candle,RawEvent
    iid='binance:perp:P33REPAIRUSDT'
    monkeypatch.setattr(collection_schedule,'due',lambda *args,**kw:['P33REPAIRUSDT'])
    monkeypatch.setattr(repository,'now_ms',lambda:NOW+H)
    with isolated.begin() as session:
        session.add(Asset(id='P33REPAIR',symbol='P33REPAIR',known_at=NOW));session.flush()
        session.add(Instrument(id=iid,asset_id='P33REPAIR',venue='binance',native_symbol='P33REPAIRUSDT',market_type='linear_perpetual',quote_asset='USDT',spec={}));session.flush()
        session.add(RawEvent(id='p33-repair-source',source='test',endpoint='test',received_at=NOW,payload={}));session.flush()
        for i in range(3):
            session.add(Candle(instrument_id=iid,resolution='240',open_time=NOW-8*H+i*4*H,close_time=NOW-4*H+i*4*H,
                is_closed=i==1,open='10',high='11',low='9',close='10',base_volume='1',quote_volume='10',received_at=NOW,raw_ref='p33-repair-source'))
    targets=repository.candle_targets('binance')
    four=next(t for t in targets if t[0]==iid and t[2]=='240')
    assert four[5]==NOW-8*H


def test_delayed_observation_is_not_a_missing_historical_entry_bar():
    from engine.strategies.p33_replay import simulate
    e,_=event();e['plan']['as_of']=NOW+1000
    e['plan']['targets']['t1']={'low':'30','known_at':NOW}
    assert simulate(e,[],24)['status']=='entry_window_elapsed_before_next_open'


def test_followup_checks_background_freshness_and_continuity():
    from engine.strategies.p33_lifecycle import qualify
    e,data=event()
    e['initial']=next(h for h in evaluate(data,NOW,'.01','test')['results'] if h['strategy_id']=='recent_high')
    good=qualify(e,None,None,None,NOW,data)
    assert all(v['status']=='fresh' for v in good['dependency_data'].values())
    stale=deepcopy(data);stale['1D']=bars([10]*60,'1D',NOW-72*H)
    result=qualify(e,None,None,None,NOW,stale)
    assert result['rule_action']['code']=='paused'
    assert 'dependency_candles_unavailable' in result['rule_action']['reason_codes']
    broken=deepcopy(data);broken['1D'].pop(-10)
    assert qualify(e,None,None,None,NOW,broken)['dependency_data']['1D']['status']=='unavailable'


def test_retest_cannot_participate_after_parent_ends():
    from engine.strategies.p33_lifecycle import qualify
    e,data=event();e['initial']['strategy_id']='retest'
    result=qualify(e,None,None,None,NOW,parent_active=False)
    assert 'parent_structure_ended' in result['rule_action']['reason_codes']


def test_new_strategy_freezes_derivatives_without_aliasing_live_inputs():
    e,data=event()
    context={'daily_oi_path':{'status':'matched','path':'增仓上涨'},'cross_market_oi':{'total_oi_usd':'100'}}
    history={'instrument_id':'test','oi':[{'time':NOW,'quantity':'5'}],'funding':[]}
    snap=make_snapshot(e,data,None,'.01',NOW,derivative_context=context,derivative_history=history)
    history['oi'][0]['quantity']='999';context['cross_market_oi']['total_oi_usd']='999'
    assert snap['derivative_history_at_snapshot']['oi'][0]['quantity']=='5'
    assert snap['derivatives']['cross_market_oi']['total_oi_usd']=='100'


def test_tracking_gap_pauses_cursor_until_original_bars_are_recovered():
    from engine.strategies.p33_lifecycle import follow,qualify
    e,_=event();tf=e['initial']['invalidation']['timeframe']
    duration=4*H if tf=='240' else H
    rows=bars([20,20,20],tf,NOW+3*duration)
    pending=follow(e,{tf:rows[1:]},NOW+3*duration)
    assert pending['coverage_gap_pending_at']==NOW
    assert 'checked_through' not in pending
    assert 'tracking_gap' in qualify(pending,None,None,None,NOW+3*duration)['rule_action']['reason_codes']
    resumed=follow(pending,{tf:rows},NOW+3*duration)
    assert 'coverage_gap_pending_at' not in resumed
    assert resumed['checked_through']==NOW+3*duration


def test_p33_equal_extreme_is_first_without_changing_legacy_default():
    from engine.pd2.analysis import pivots,technical
    rows=bars([1,2,4,4,2,1])
    assert pivots(rows,'high')[0]['index']==3
    assert pivots(rows,'high',equal='first')[0]['index']==2
    assert technical(rows,'240',NOW,'.01',pivot_tie='first')['pivot_tie_rule']=='first'
    assert technical(rows,'240',NOW,'.01')['pivot_tie_rule']=='last'
