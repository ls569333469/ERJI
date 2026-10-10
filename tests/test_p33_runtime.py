from copy import deepcopy
from types import SimpleNamespace as N
from sqlalchemy import select,func
import engine.signal_service as scanner
from engine.models import RawEvent,UserPreference,Plan,CurrentSignal
from engine.strategies.p33_runtime import observe,advance
from engine.pd2.config import ExecutionConfig
from test_p25_signals import isolated
from test_p33_patterns import base,NOW,H


def test_forward_observer_is_idempotent_and_never_publishes_buy(isolated):
    instrument=N(id='p33:test:runtime',spec={'tick_size':'.01'})
    data=base()
    with isolated.begin() as s:
        before=(s.scalar(select(func.count()).select_from(Plan)),s.scalar(select(func.count()).select_from(CurrentSignal)))
        observe(s,instrument,data,NOW);s.flush()
        record=s.get(UserPreference,'p33_shadow:'+instrument.id).payload
        assert record['summary']['main_strategy']=='double_bottom'
        assert any(r['strategy_id']=='recent_high' and r['status']=='triggered' for r in record['result']['results'])
        event_id=record['result']['main']['event_id']
        frozen=deepcopy(record['active_events'][event_id]['initial'])
        count=s.scalar(select(func.count()).select_from(RawEvent).where(RawEvent.endpoint=='p33_first'))
        observe(s,instrument,data,NOW+1000);s.flush()
        assert s.scalar(select(func.count()).select_from(RawEvent).where(RawEvent.endpoint=='p33_first'))==count
        observe(s,instrument,data,NOW+4*H+1);s.flush()
        event=s.get(UserPreference,'p33_shadow:'+instrument.id).payload['active_events'][event_id]
        assert event['entry_expired_at']==NOW+4*H+1 and event['initial']==frozen
        assert event['trading_enabled'] is False
        assert before==(s.scalar(select(func.count()).select_from(Plan)),s.scalar(select(func.count()).select_from(CurrentSignal)))


def test_closed_followup_invalidates_without_rewriting_first_trigger():
    event={'initial':{'trigger':{'close_time':NOW},'expires_at':NOW+H,'invalidation':{'timeframe':'240','price':'10'},'protection':'9'},'status':'triggered'}
    row={'open_time':NOW,'close_time':NOW+4*H,'received_at':NOW+4*H+100,'is_closed':True,'low':'8','close':'9.5'}
    assert advance(event,{'240':[row]},NOW+4*H).get('invalidated_at') is None
    result=advance(event,{'240':[row]},NOW+4*H+100)
    assert result['invalidated_at']==NOW+4*H+100
    assert result['invalidation_close_time']==NOW+4*H
    assert result['protection_bar_open']==NOW
    assert result['initial']==event['initial']
    assert advance(result,{'240':[]},NOW+8*H)==result


def test_missing_tick_and_late_history_do_not_generate_trigger(isolated):
    with isolated.begin() as s:
        instrument=N(id='p33:test:missing',spec={})
        observe(s,instrument,base(),NOW);s.flush()
        payload=s.get(UserPreference,'p33_shadow:'+instrument.id).payload
        assert payload['active_events']=={} and payload['result']['trading_enabled'] is False
        instrument.spec={'tick_size':'.01'}
        data=base()
        for row in data['1D']:row['received_at']=NOW
        observe(s,instrument,data,NOW);s.flush()
        result=s.get(UserPreference,'p33_shadow:'+instrument.id).payload['result']
        assert next(r for r in result['results'] if r['strategy_id']=='recent_high')['status']=='missing_data'


def test_plan_and_group_revisions_are_frozen_and_terminal_groups_do_not_reopen(isolated,monkeypatch):
    import engine.strategies.p33_runtime as runtime
    from engine.strategies.p33 import evaluate
    data=base();instrument=N(id='binance:perp:P33GROUPTEST',spec={'tick_size':'.01'})
    actual=evaluate(data,NOW,'.01',instrument.id)
    first=deepcopy(actual['main'])
    high_priority=deepcopy(first);high_priority.update(strategy_id='ath',event_id=first['event_id']+'-ath')
    result={'main':first,'auxiliary_hits':[],'results':[first]}
    monkeypatch.setattr(runtime,'evaluate',lambda *a,**k:deepcopy(result))
    with isolated.begin() as s:
        before=s.scalar(select(func.count()).select_from(Plan))
        observe(s,instrument,data,NOW,execution_config=ExecutionConfig());s.flush()
        saved=s.get(UserPreference,'p33_shadow:'+instrument.id).payload
        group=next(iter(saved['groups'].values()));initial=deepcopy(group['versions'][0])
        assert len(saved['groups'])==1 and initial['revision']==1
        result.update(main=high_priority,auxiliary_hits=[first],results=[first,high_priority])
        data['240'][0]['revision']=2
        observe(s,instrument,data,NOW+1000,execution_config=ExecutionConfig(confirmed_min_net_r=4));s.flush()
        saved=s.get(UserPreference,'p33_shadow:'+instrument.id).payload
        group=saved['groups'][group['group_id']]
        assert len(saved['groups'])==1 and len(group['versions'])==2
        assert group['versions'][0]==initial and group['versions'][1]['strategy_id']=='ath'
        assert saved['active_events'][first['event_id']]['execution_config']['confirmed_min_net_r']==1.8
        for event in saved['active_events'].values():
            assert event['qualification']['trading_enabled'] is False
        bar={**data['240'][-1],'open_time':NOW,'close_time':NOW+4*H,'received_at':NOW+4*H,'low':'0.1','close':'0.2'}
        data['240'].append(bar)
        observe(s,instrument,data,NOW+4*H);s.flush()
        saved=s.get(UserPreference,'p33_shadow:'+instrument.id).payload
        assert saved['active_events']=={} and saved['groups'][group['group_id']]['status']=='ended'
        observe(s,instrument,data,NOW+4*H+1000);s.flush()
        assert s.get(UserPreference,'p33_shadow:'+instrument.id).payload['active_events']=={}
        assert s.scalar(select(func.count()).select_from(Plan))==before


def test_expired_input_rechecks_freshness_even_without_new_bars(isolated):
    instrument=N(id='p33:test:freshness',spec={'tick_size':'.01'})
    with isolated.begin() as s:
        observe(s,instrument,base(),NOW);s.flush()
        key='p33_shadow:'+instrument.id
        first=s.get(UserPreference,key).payload['input_signature']
        observe(s,instrument,base(),NOW+5*H);s.flush()
        payload=s.get(UserPreference,key).payload
        assert payload['input_signature']!=first
        assert all(r['status']=='missing_data' for r in payload['result']['results'])


def test_missing_one_hour_or_quote_does_not_skip_independent_four_hour_research(isolated,monkeypatch):
    from engine.models import Asset,Instrument
    from engine.contracts import RuntimeConfig
    iid='binance:perp:P33DEPSUSDT';asset='P33DEPS'
    with isolated.begin() as s:
        s.add(Asset(id=asset,symbol=asset,known_at=NOW));s.flush()
        s.add(Instrument(id=iid,asset_id=asset,venue='binance',native_symbol=asset+'USDT',market_type='linear_perpetual',quote_asset='USDT',spec={'tick_size':'.01'}))
    market=N(instrument_id=iid,candles_count=60,in_universe=True,venue='binance',quality=N(status='stale'),
             model_dump=lambda **k:{'instrument_id':iid,'quote_asset':'USDT'})
    monkeypatch.setattr(scanner,'list_markets',lambda *a,**k:N(rows=[market]))
    monkeypatch.setattr(scanner,'runtime_config',lambda:RuntimeConfig())
    monkeypatch.setattr(scanner,'now_ms',lambda:NOW)
    monkeypatch.setattr(scanner,'_recent_bars',lambda *a,**k:{(iid,tf):rows for tf,rows in base().items()})
    counts=scanner.scan_signals([iid])
    assert counts['quote_stale']==1 and counts['scanned']==0 and counts['shadow_errors']==0
    with isolated() as s:
        saved=s.get(UserPreference,'p33_shadow:'+iid).payload
        assert next(r for r in saved['result']['results'] if r['strategy_id']=='recent_high')['status']=='triggered'
        assert next(r for r in saved['result']['results'] if r['strategy_id']=='early_strength')['status']=='missing_data'
