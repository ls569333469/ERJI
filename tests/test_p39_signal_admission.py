"""Regression of real admission blockers, using a disposable database only."""
from copy import deepcopy
from decimal import Decimal as D
import time
import pytest
from sqlalchemy import select
from engine.pd2.decision import execution_cost, decide
from engine.pd2 import signal_projection as projection, service
from engine.p37 import store, worker
from engine.p37.analysis import aggregate, SOURCE_5M_BARS
from engine.p38 import jobs
from engine.models import Observation, SignalSnapshot, Plan, CurrentSignal, MarketState, Instrument
from test_p25_signals import isolated
from test_p39_reliability import seed_plan
from test_p33_lifecycle import research_event, NOW
from engine.strategies.p33_lifecycle import qualify
from engine.pd2.config import ExecutionConfig


def book(now,price='10'):
    return dict(scope='execution_market',observed_at=now,received_at=now,
                asks=[[price,'10000']],bids=[[str(D(price)*D('.9999')),'10000']])


@pytest.mark.parametrize('target,fee,fund',[
    ('10.1','.0005','.001'),('10.001','.0005','.001'),
    (None,'.0005','.001'),('9','.0005','.001'),('10.1',None,'.001'),('10.1','.0005',None)])
def test_reference_estimates_do_not_veto_valid_entry(target,fee,fund):
    result=execution_cost('10','9',target,book(1000),1000,fee_rate=fee,funding_cost_rate=fund)
    assert result['status']=='eligible' and result['reasons']==[]
    assert result['cost_policy']=='reference-only-v1'
    if target in ('10.1','10.001') and fee is not None and fund is not None:
        assert result['economic_status']=='below_reference' and D(result['net_r'])<D('1.5')
    else:
        assert result['net_r'] is None and result['economic_status']=='unavailable'
    evidence=dict(trigger=True,entry_zone=['9.9','10.2'],protection='9',expires_at=20000)
    action=decide(evidence,'structure_confirmed',dict(price='10',price_kind='ask',as_of=1000),1000,result)
    assert action['code']=='participate'


@pytest.mark.parametrize('scenario,expected',[
    ('stale','stale'),('depth','insufficient_depth'),('spread','wide_spread'),
    ('foreign','unavailable'),('stop','unavailable')])
def test_native_execution_checks_remain_required(scenario,expected):
    b=book(1000);stop='9';now=1000
    if scenario=='stale':now=91001
    if scenario=='depth':b['asks']=[['10','1']]
    if scenario=='spread':b['bids']=[['9','10000']]
    if scenario=='foreign':b['scope']='aggregate'
    if scenario=='stop':stop='11'
    result=execution_cost('10',stop,None,b,now)
    assert result['status']==expected


def test_ath_without_target_qualifies_and_expiry_zone_still_apply():
    e=research_event();e['initial']['strategy_id']='ath';e['plan']['targets']['t1']=None
    iid=e['initial']['market_id'];m=dict(instrument_id=iid,quote_asset='USDT',quote_usd_rate='1')
    b={**book(NOW), 'instrument_id':iid}
    assert qualify(e,m,b,ExecutionConfig(),NOW)['rule_action']['code']=='participate'
    late=deepcopy(e);late['initial']['expires_at']=NOW-1
    assert qualify(late,m,b,ExecutionConfig(),NOW)['rule_action']['code']=='expired'
    assert qualify(e,m,{**book(NOW,'12'),'instrument_id':iid},ExecutionConfig(),NOW)['rule_action']['code']=='no_chase'
    assert qualify(e,m,{**b,'observed_at':NOW-20000},ExecutionConfig(),NOW)['rule_action']['code']=='participate'
    assert qualify(e,m,{**b,'observed_at':NOW-90001},ExecutionConfig(),NOW)['rule_action']['code']=='paused'


@pytest.mark.parametrize('offset',range(6))
def test_complete_30m_history_at_every_5m_offset(offset):
    rows=[dict(open_time=(n+offset)*300000,close_time=(n+offset+1)*300000,close='10',volume=D('1')) for n in range(SOURCE_5M_BARS)]
    assert len(aggregate(rows,1800000))>=300
    if offset:assert len(aggregate(rows[-1800:],1800000))==299
    gap=deepcopy(rows);missing=gap.pop(60)['open_time']//1800000*1800000
    assert all(r['open_time']!=missing for r in aggregate(gap,1800000))


def test_structure_runs_before_trend_then_trend_progresses(isolated):
    at=int(time.time()*1000)
    jobs.submit(['older-trend'],at=at-10000,work_kind='trend')
    jobs.submit(['new-trigger'],at=at,work_kind='structure')
    first=jobs.take(at,work_kinds=jobs.LANES['structure'])
    assert first['work_kind']=='structure'
    assert jobs.finish(first,at=at+1)
    second=jobs.take(at+2,work_kinds=jobs.LANES['trend'])
    assert second['work_kind']=='trend'
    assert jobs.finish(second,at=at+3)


@pytest.mark.parametrize('state',['expired','ended','open'])
def test_old_protection_does_not_take_new_entry_priority(isolated,state):
    at=int(time.time()*1000)
    with isolated.begin() as s:
        iid,_=seed_plan(s,at,expired=state=='expired')
        if state=='ended':
            old=s.get(SignalSnapshot,'p39-old');old.payload={**old.payload,'action':{'code':'ended'}}
        s.add(Observation(id='admission-test',instrument_id=iid,asset_id='P39TEST',direction='up',
                          first_at=at,last_at=at,visible_at=at,status='observing',version=at,payload={}))
    from engine.p37.detector import DEFAULTS
    store.refresh_tracking(at,DEFAULTS)
    with isolated() as s:
        row=s.get(Observation,'admission-test')
        assert row.payload['trade_status']==('active' if state=='open' else 'no_entry')
        assert row.payload['plan_tracking_status']=={'expired':'protection_only','ended':'none','open':'entry_open'}[state]
        if state=='expired':assert row.status=='focused'
        assert s.get(Plan,'p39-p') is not None


@pytest.mark.parametrize('target',['10.1',None])
def test_valid_frozen_plan_reaches_buy_api_without_r_or_target_gate(isolated,monkeypatch,target):
    at=int(time.time()*1000)
    with isolated.begin() as s:
        iid,_=seed_plan(s,at)
        p=s.get(Plan,'p39-p')
        p.payload={**p.payload,'original_t1':{'low':target} if target else None,
                   'opportunity':{'entry_obstructions':[{'low':'10','high':'11'}]}}
    monkeypatch.setattr(service,'now_ms',lambda:at)
    monkeypatch.setattr(projection,'now_ms',lambda:at)
    assert service.refresh_current([iid])['snapshots']==1
    with isolated() as s:
        snap=s.scalar(select(SignalSnapshot).order_by(SignalSnapshot.as_of.desc()).limit(1)).payload
        assert snap['action']['code']=='participate'
        assert snap['execution']['status']=='eligible'
        if target:assert D(snap['execution']['net_r'])<D('1.8')
        else:assert snap['execution']['net_r'] is None
    projection.publish([snap],scope=[iid],built_at=at)
    response=projection.read('buy')
    assert any(row['instrument_id']==iid for row in response['rows'])


def test_missing_target_candidate_stays_waiting_instead_of_becoming_buy():
    row=dict(id='waiting',asset_id='TEST',instrument_id='binance:perp:TESTUSDT',
             phase='structure_pending',phase_label='等待突破',fresh=True,
             market=dict(symbol='TEST',venue='binance',quote_asset='USDT',price='100',as_of=NOW),
             action=dict(code='waiting',reason='waiting'),first_seen_at=NOW,first_seen_price='100',
             evidence=dict(box=dict(upper='104',lower='90',trigger_price='105',atr='4')))
    c=projection.compact(row)
    assert c['opportunity_eligible'] is True and c['action_code']=='waiting'
    assert c['preliminary_net_r'] is None and c['targets']['t1'] is None


@pytest.mark.parametrize('target_present',[True,False])
def test_p33_recognition_qualification_publication_reaches_buy_api(isolated,monkeypatch,target_present):
    from test_p33_patterns import base
    from engine.strategies.p33 import evaluate
    from engine.strategies.p33_lifecycle import freeze_plan
    from engine.strategies.p33_delivery import make_snapshot, publish
    from engine.repository import runtime_config
    from engine.pd2 import portfolio
    # A configured research balance must not make absent cost estimates crash
    # publication; sizing remains advisory when the estimate cannot be made.
    monkeypatch.setattr(portfolio,'portfolio',lambda:{'risk':{'status':'available','max_new_plan_risk_usd':'10'}})
    with isolated.begin() as s:
        iid,_=seed_plan(s,NOW)
        data=base();hit=next(h for h in evaluate(data,NOW,'.01',iid)['results'] if h['strategy_id']=='recent_high')
        assert hit['status']=='triggered'
        price=str((D(hit['entry_zone'][0])+D(hit['entry_zone'][1]))/2)
        plan=freeze_plan(hit,data,NOW,'.01')
        plan['targets']['t1']={'low':str(D(price)+D('.001')),'known_at':NOW} if target_present else None
        e=dict(initial=hit,plan=plan,first_observed_at=NOW,status='triggered')
        m=dict(instrument_id=iid,asset_id='P39TEST',venue='binance',symbol='P39TEST',
               quote_asset='USDT',quote_usd_rate='1',price=price,as_of=NOW)
        e['qualification']=qualify(e,m,{**book(NOW,price),'instrument_id':iid},ExecutionConfig(),NOW,data)
        assert e['qualification']['rule_action']['code']=='participate'
        snapshot=make_snapshot(e,data,m,'.01',NOW)
        assert publish(s,snapshot,e,s.get(Instrument,iid),runtime_config(s),NOW,'admission-group')['enabled']
        s.flush()
        snap=s.scalar(select(SignalSnapshot).where(SignalSnapshot.plan_id==hit['event_id']+':p33')).payload
        s.get(MarketState,iid).payload={'price':price,'price_kind':'last'}
    monkeypatch.setattr(projection,'now_ms',lambda:NOW)
    projection.publish([snap],scope=[iid],built_at=NOW)
    assert any(r['instrument_id']==iid for r in projection.read('buy')['rows'])


def test_priority_slot_freed_after_expiry_without_deleting_protection(isolated):
    from engine.p37.detector import DEFAULTS
    from engine.models import UserPreference
    at=int(time.time()*1000)
    with isolated.begin() as s:
        iid,_=seed_plan(s,at,expired=True)
        from test_p39_remaining import native_market
        native_market(s,'binance:perp:NEWUSDT','NEW')
        s.add(Observation(id='expired-priority',instrument_id=iid,asset_id='P39TEST',direction='up',
                          first_at=at,last_at=at,visible_at=at,status='focused',version=at,
                          payload={'trade_status':'active'}))
        s.add(Observation(id='new-priority',instrument_id='binance:perp:NEWUSDT',asset_id='NEW',direction='up',
                          first_at=at,last_at=at,visible_at=at,status='observing',version=at,payload={}))
        s.merge(UserPreference(key='p37_active',updated_at=at-1,payload={'instrument_ids':[iid],'admitted_at':{iid:at-1}}))
    store.refresh_tracking(at,DEFAULTS)
    chosen,overflow=worker.select_work(at,1)
    assert chosen==[('new-priority','binance:perp:NEWUSDT')] and overflow==0
    with isolated() as s:
        assert s.get(Observation,'expired-priority').status=='focused'
        assert s.get(Plan,'p39-p') is not None
