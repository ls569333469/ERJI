"""Remaining P39 contracts: real qualification, durable admission and publication."""
import asyncio
from copy import deepcopy
from decimal import Decimal as D
from sqlalchemy import select
import pytest
from engine.models import Observation,UserPreference,Task,SignalSnapshot,DataCapability,MarketState,CurrentSignal
from engine.p38 import jobs
from engine.p37 import worker
from engine.p38.observation_query import read_observations
from engine.strategies.p33 import Facts,recognize,Missing
from test_p25_signals import isolated
from test_p39_reliability import seed_plan
from test_p33_patterns import base,NOW,H


@pytest.mark.parametrize('fx',['fresh','absent','stale'])
def test_fast_p33_actual_fx_qualification_preserves_frozen_plan(isolated,monkeypatch,fx):
    from test_p33_completion import event
    from engine.models import Plan
    from engine.pd2 import service
    from engine.pd2.config import ExecutionConfig
    original,bars=event()
    with isolated.begin() as s:
        iid,old=seed_plan(s,NOW,p33=True)
        initial=original['initial'];initial.update(market_id=iid,entry_zone=['9.99','10.01'],protection='8',expires_at=NOW+60000)
        original['plan'].update(market_id=iid,entry_zone=['9.99','10.01'],original_protection='8',expires_at=NOW+60000,
            targets={'t1':{'low':'20','high':'20','known_at':NOW-1}})
        original['execution_config']=ExecutionConfig().model_dump(mode='json')
        s.add(UserPreference(key='p33_shadow:'+iid,updated_at=NOW-1,payload={'active_events':{'p39-e':original}}))
        cap=s.get(DataCapability,'binance:execution_book:'+iid);cap.payload={**cap.payload,'instrument_id':iid}
        conversion=s.get(DataCapability,'binance:fx:USDTUSD')
        if fx=='absent':s.delete(conversion)
        elif fx=='stale':conversion.observed_at=NOW-3600001
        frozen=deepcopy(s.get(Plan,'p39-p').payload)
    monkeypatch.setattr(service,'load_bars',lambda *a:bars)
    monkeypatch.setattr(service,'now_ms',lambda:NOW)
    service.refresh_current([iid])
    with isolated() as s:
        current=s.scalar(select(SignalSnapshot).where(SignalSnapshot.plan_id=='p39-p').order_by(SignalSnapshot.as_of.desc())).payload
        assert current['action']['code']==('participate' if fx=='fresh' else 'paused')
        assert (current['market']['quote_usd_rate'] is not None)==(fx=='fresh')
        if fx=='fresh':assert current['valid_until']==NOW+60000  # Original plan expires before the current 90s quote/book TTL.
        else:assert '报价币USD换算缺失' in current['execution']['reasons']
        saved=s.get(UserPreference,'p33_shadow:'+iid).payload['active_events']['p39-e']
        assert saved['qualification']==current['research_qualification']
        assert s.get(Plan,'p39-p').payload==frozen and s.get(SignalSnapshot,'p39-old').payload==old
    if fx=='fresh':
        from engine import repository
        from engine.pd2 import signal_projection
        monkeypatch.setattr(repository,'now_ms',lambda:NOW)
        monkeypatch.setattr(signal_projection,'now_ms',lambda:NOW)
        service.decision_list(instrument_ids=[iid])
        assert any(r['instrument_id']==iid and r['action_code']=='participate' for r in signal_projection.read('opportunities',size=300)['rows'])


def observation(ident,iid,asset,at,pinned=False,active=False):
    return Observation(id=ident,instrument_id=iid,asset_id=asset,direction='up',first_at=at,visible_at=at,last_at=at,status='observing',version=at,
        payload={'symbol':asset,'pinned':pinned,'trade_status':'active' if active else 'no_entry','analysis_checked_at':at,'visible_price':'10'})

def native_market(session,iid,asset):
    from engine.models import Instrument,Asset
    if not session.get(Asset,asset):session.add(Asset(id=asset,symbol=asset,known_at=1));session.flush()
    session.add(Instrument(id=iid,asset_id=asset,venue='binance',native_symbol=asset+'USDT',market_type='linear_perpetual',quote_asset='USDT',spec={'base_asset':asset}));session.flush()


def test_structure_cannot_be_evicted_by_trend_completion_and_slots_rotate(isolated):
    with isolated.begin() as s:
        native_market(s,'a','A');native_market(s,'b','B')
        s.add(observation('a','a','A',NOW));s.add(observation('b','b','B',NOW-1))
    chosen,_=worker.select_work(NOW,1);assert chosen==[('a','a')]
    for kind in ('trend','structure'):jobs.submit(['a'],at=NOW,work_kind=kind)
    lease=jobs.take(NOW,work_kinds=['trend']);jobs.finish(lease,at=NOW+1)
    assert worker.select_work(NOW+2,1)[0]==[('a','a')]
    lease=jobs.take(NOW+2,work_kinds=['structure']);jobs.finish(lease,at=NOW+3)
    with isolated.begin() as s:
        r=s.get(Observation,'a');r.payload={**r.payload,'structure_checked_at':NOW+3}
    assert worker.select_work(NOW+4,1)[0]==[('b','b')]
    # A fresh trend clock no longer suppresses first structure submission.
    jobs.submit(['b'],at=NOW+4,work_kind='structure')
    assert jobs.take(NOW+4,work_kinds=['structure'])['instrument_id']=='b'


def test_ineligible_claim_is_retired_without_false_completion_then_readmitted(isolated,monkeypatch):
    monkeypatch.setattr(jobs,'now_ms',lambda:NOW)
    jobs.submit(['x'],at=NOW,work_kind='structure');lease=jobs.take(NOW,work_kinds=['structure'])
    asyncio.run(jobs.execute(lease,None))
    with isolated() as s:
        p=s.get(Task,lease['id']).payload
        assert p['retired_reason']=='outside_active_slots' and p['completed_count']==0 and not p.get('completed_version')
    jobs.submit(['x'],at=NOW,work_kind='structure')
    assert jobs.take(NOW,work_kinds=['structure']) is not None


def test_page_uses_same_market_as_selector_and_preserves_related_events(isolated):
    with isolated.begin() as s:
        native_market(s,'spot','A');native_market(s,'perp','A')
        s.add(observation('spot','spot','A',NOW,pinned=True));s.add(observation('perp','perp','A',NOW+1))
    assert worker.select_work(NOW+2,1)[0]==[('spot','spot')]
    result=read_observations()
    row=next(r for r in result['rows'] if r['asset_id']=='A')
    assert row['instrument_id']=='spot' and row['analysis_instrument_id']=='spot'
    assert row['related_events'][0]['instrument_id']=='perp'
    assert row['structure_task_status']=='not_submitted'


def test_late_history_is_distinguished_from_source_stale_without_lookahead():
    data=base()
    for b in data['1D']:b['received_at']=NOW
    hit=recognize('recent_high',Facts(data,NOW,'.01'),'test')
    assert hit['status']=='missing_data' and hit['reason_code']=='history_not_known_before_trigger'
    # Both original pre-trigger causality and today's receive clock remain strict.
    assert recognize('recent_high',Facts(base(),NOW,'.01'),'test')['status']=='triggered'
    for b in data['1D']:b['received_at']=NOW+1
    assert recognize('recent_high',Facts(data,NOW,'.01'),'test')['reason_code']=='history_insufficient'
    with pytest.raises(Missing) as err:Facts(base(),NOW+49*H,'.01').get('1D',30)
    assert err.value.code=='closed_candles_stale'
    data=base();data['240'].pop(-10)
    assert recognize('recent_high',Facts(data,NOW,'.01'),'test')['reason_code']=='candle_gap'


def test_projection_version_changes_on_decision_even_with_same_quote(isolated):
    with isolated.begin() as s:iid,_=seed_plan(s,NOW)
    before=jobs.dependency_versions([iid],NOW,'projection')[iid]
    with isolated.begin() as s:s.add(SignalSnapshot(id='p39-new-decision',plan_id='p39-p',as_of=NOW,payload={'action':{'code':'waiting'}}))
    assert jobs.dependency_versions([iid],NOW,'projection')[iid]!=before
    before=jobs.dependency_versions([iid],NOW,'projection')[iid]
    with isolated.begin() as s:s.get(MarketState,iid).as_of=NOW+1000
    assert jobs.dependency_versions([iid],NOW+1000,'projection')[iid]==before


def test_new_quote_does_not_cancel_closed_structure_plan_refresh(isolated,monkeypatch):
    from engine.pd2 import service
    from engine import repository
    with isolated.begin() as s:iid,_=seed_plan(s,NOW)
    original=service.list_markets
    monkeypatch.setattr(repository,'now_ms',lambda:NOW)
    monkeypatch.setattr(service,'now_ms',lambda:NOW)
    def capture_then_tick(ids):
        result=original(ids)
        with isolated.begin() as s:s.get(MarketState,iid).as_of=NOW+1
        return result
    monkeypatch.setattr(service,'list_markets',capture_then_tick)
    assert not service.refresh_plans([iid]).get('superseded')


def test_qualifying_current_reaches_real_projection_and_safe_api_before_expiry(isolated,monkeypatch):
    from engine.pd2 import service,signal_projection
    from engine import repository
    with isolated.begin() as s:
        iid,old=seed_plan(s,NOW)
        # Same frozen fixture plan, plus fields carried by a real full snapshot.
        snap=s.get(SignalSnapshot,'p39-old');snap.payload={**snap.payload,'first_seen_at':NOW-10000,'first_seen_price':'9','phase_label':'结构确认'}
        s.add(UserPreference(key='main_market:P39TEST',updated_at=NOW,payload={'selected':iid}))
    monkeypatch.setattr(service,'now_ms',lambda:NOW)
    monkeypatch.setattr(repository,'now_ms',lambda:NOW)
    monkeypatch.setattr(signal_projection,'now_ms',lambda:NOW)
    monkeypatch.setattr(jobs,'now_ms',lambda:NOW)
    from engine.pd2 import market_selection
    monkeypatch.setattr(market_selection,'select_main',lambda *a:None)
    class ExistingBook:
        async def collect(self,target):assert target==iid
    jobs.submit([iid],at=NOW,work_kind='entry');lease=jobs.take(NOW,work_kinds=['entry'])
    asyncio.run(jobs.execute(lease,ExistingBook()))
    with isolated() as s:
        assert s.get(Task,lease['id']).state=='complete'
        assert s.get(Task,jobs.identity(iid,'projection')) is None
    result=signal_projection.read('opportunities',size=300)
    assert any(r['instrument_id']==iid and r['action_code']=='participate' for r in result['rows'])
    with isolated() as s:expires=s.scalar(select(CurrentSignal.valid_until).where(CurrentSignal.instrument_id==iid))
    monkeypatch.setattr(signal_projection,'now_ms',lambda:expires+1)
    assert not any(r['instrument_id']==iid and r['action_code']=='participate' for r in signal_projection.read('opportunities',size=300)['rows'])
