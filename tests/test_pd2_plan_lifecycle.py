from copy import deepcopy
from decimal import Decimal as D
from types import SimpleNamespace
import pytest
from sqlalchemy import select
from engine.models import Asset,Instrument,Campaign,Episode,DataCapability,Plan,SignalSnapshot
from engine.pd2 import service,derivatives
from engine.contracts import RuntimeConfig
from test_pd2_persistence import isolated

@pytest.mark.parametrize('template,phase,expected',[
 ('early_breakout','early_trigger','trial'),('confirmed_breakout','structure_confirmed','participate'),
 ('retest_entry','structure_confirmed','participate'),('trend_restart','trend_continuing','participate')])
def test_templates_persist_positive_freeze_expire_and_never_revive(isolated,monkeypatch,template,phase,expected):
    clock=[1000000000];now=clock[0];config=RuntimeConfig()
    monkeypatch.setattr(service,'runtime_config',lambda:config)
    monkeypatch.setattr(service,'now_ms',lambda:clock[0])
    asset='PD2_TEST_'+template;instrument='test:'+asset;episode=asset;campaign=asset
    market=SimpleNamespace(asset_id=asset,instrument_id=instrument,venue='binance',quote_asset='USD',price=D('1'),quality=SimpleNamespace(status='fresh'),as_of=now)
    market.model_dump=lambda **kw:{'price':str(market.price),'asset_id':asset,'symbol':asset,'instrument_id':instrument,'quote_asset':'USD'}
    monkeypatch.setattr(service,'list_markets',lambda *args:SimpleNamespace(rows=[market]))
    monkeypatch.setattr(service,'load_bars',lambda *a,**kw:{'60':[],'240':[],'1D':[],'1W':[]})
    level={'id':'resistance','kind':'resistance','low':'1.4','high':'1.5','timeframe':'240','method':'confirmed_pivot','known_at':now-1000,'label':'测试确认高点'}
    monkeypatch.setattr(service,'technical',lambda bars,tf,*a:{'timeframe':tf,'status':'fresh','direction':'up','levels':[level] if tf=='240' else [],'latest':{'close':'1'},'series':[],'volume_multiple':'2'})
    monkeypatch.setattr(derivatives,'context_for',lambda *a:{})
    monkeypatch.setattr(service,'derivative_history',lambda *a:{})
    evidence={'template':template,'instrument_id':instrument,'entry_zone':['.99','1.01'],'protection':'.9','expires_at':now+100000,'trigger':{'close_time':now-100,'resolution':'60'},'box':{'upper':'.98','lower':'.8','end_time':now-1000,'start_time':now-5000,'atr':'.02'},'reason':'受控有效触发'}
    if template!='early_breakout':evidence['confirmation']=now-100
    with isolated.begin() as s:
        s.add(Asset(id=asset,symbol=asset,known_at=now));s.flush()
        s.add(Instrument(id=instrument,asset_id=asset,venue='binance',native_symbol=asset,market_type='linear_perpetual',quote_asset='USD',spec={'tick_size':'.001'}));s.flush()
        s.add(Campaign(id=campaign,asset_id=asset,first_seen_at=now-5000,first_seen_price=D('.98'),reference_instrument_id=instrument,state='active'));s.flush()
        s.add(Episode(id=episode,campaign_id=campaign,strategy_id=template,phase=phase,rule_version=service.VERSION,evidence=evidence))
    def book(price='1'):
        with isolated.begin() as s:s.merge(DataCapability(key='binance:execution_book:'+instrument,provider='binance',capability='execution_book',subject=instrument,status='fresh',received_at=clock[0],observed_at=clock[0],expires_at=clock[0]+10000,payload={'scope':'execution_market','received_at':clock[0],'observed_at':clock[0],'asks':[[price,'10000']],'bids':[[str(D(price)-D('.001')),'10000']]}))
    def latest():
        with isolated() as s:return s.scalar(select(SignalSnapshot).where(SignalSnapshot.plan_id.like(episode+'%')).order_by(SignalSnapshot.as_of.desc())).payload
    book();service.refresh_plans([instrument]);first=latest()
    assert first['action']['code']==expected and first['probability'] is None
    with isolated() as s:frozen=deepcopy(s.get(Plan,first['plan_id']).payload)
    # A new published configuration cannot rewrite the existing plan.
    config.execution.confirmed_min_net_r=10;clock[0]+=20000;market.as_of=clock[0];book('1.1');service.refresh_plans([instrument]);assert latest()['action']['code']=='no_chase'
    clock[0]+=20000;market.as_of=clock[0];book('1');
    with isolated.begin() as s:
        c=s.get(DataCapability,'binance:execution_book:'+instrument);c.payload={**c.payload,'asks':[['1','1']]}
    service.refresh_plans([instrument]);assert latest()['action']['code']=='paused'
    clock[0]=now+100001;market.as_of=clock[0];book();service.refresh_plans([instrument]);assert latest()['action']['code']=='expired'
    clock[0]+=20000;market.as_of=clock[0];market.price=D('.89');book('.89');service.refresh_plans([instrument]);assert latest()['action']['code']=='ended'
    clock[0]+=20000;market.as_of=clock[0];market.price=D('1');book();service.refresh_plans([instrument]);assert latest()['action']['code']=='ended'
    with isolated() as s:
        assert s.get(Plan,first['plan_id']).payload==frozen
        assert s.get(Campaign,campaign).first_seen_at==now-5000
