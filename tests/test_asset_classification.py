"""Product labels never join stock exposure to a crypto/spot asset identity."""
from copy import deepcopy
import pytest
from engine.asset_classification import classification
from engine.models import Observation,Instrument
from engine.p38.observation_query import read_observations
from engine.pd2.signal_projection import read
from test_p25_signals import isolated,seed,NOW


@pytest.mark.parametrize('symbol,kind,label',[
    ('CRCLB','equity_token','股票代币'),('QQQB','equity_etf_token','股票ETF代币'),
    ('SNXXB','equity_etf_token','股票ETF代币'),('ARB','crypto','加密资产'),
    ('TRB','crypto','加密资产'),('BNB','crypto','加密资产')])
def test_official_metadata_not_suffix_classifies_product(symbol,kind,label):
    value=classification('binance_spot:spot:'+symbol+'USDT',symbol)
    assert value['asset_class']==kind and value['asset_class_label']==label
    assert value['asset_class_source'].startswith('https://www.binance.com/')
    assert value['asset_class_as_of']>0


def test_identity_is_market_scoped_and_does_not_guess_unknown_stock():
    assert classification('hyperliquid:perp:CRCLB','CRCLB')['asset_class']=='unknown'
    assert classification('binance_spot:spot:UNKNOWNBUSDT','UNKNOWNB')['asset_class']=='unknown'
    value=classification('binance:perp:CRCLUSDT','CRCL',{'underlyingType':'EQUITY'})
    assert value['asset_class']=='equity_derivative'


def test_observation_and_projection_labels_preserve_native_and_event_facts(isolated,monkeypatch):
    from engine.pd2 import signal_projection
    from engine.models import CurrentSignal
    seed(isolated,action='waiting')
    iid='binance:perp:P25TEST0USDT'
    with isolated.begin() as s:
        inst=s.get(Instrument,iid);inst.spec={'base_asset':'CRCLB'}
        facts={'symbol':'CRCLB','first_price':'81','timeline':[{'at':NOW,'price':'81','reasons':[]}],'price':'82'}
        s.add(Observation(id='class-observation',instrument_id=iid,asset_id='P25TEST0',direction='up',
                          first_at=NOW,visible_at=NOW,last_at=NOW,status='observing',version=NOW,payload=facts))
        projection_before=deepcopy(s.get(CurrentSignal,'p25plan0').payload)
    row=next(r for r in read_observations()['rows'] if r['id']=='class-observation')
    assert row['asset_class']=='equity_token' and row['underlying_symbol']=='CRCL'
    assert row['instrument_id']==iid and row['asset_id']=='P25TEST0' and row['first_price']=='81'
    monkeypatch.setattr(signal_projection,'now_ms',lambda:NOW)
    result=read('opportunities',size=300)
    assert next(r for r in result['rows'] if r['instrument_id']==iid)['asset_class']=='equity_token'
    with isolated() as s:
        assert s.get(Observation,'class-observation').payload==facts
        assert s.get(CurrentSignal,'p25plan0').payload==projection_before
