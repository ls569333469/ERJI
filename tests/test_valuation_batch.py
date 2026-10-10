from decimal import Decimal as D
from types import SimpleNamespace as NS
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from engine.models import Asset,Instrument,DataCapability
from engine.pd2.providers import valuation_inputs, valuation_for


@pytest.fixture
def db():
    engine=create_engine('sqlite://')
    Asset.__table__.create(engine)
    Instrument.__table__.create(engine)
    DataCapability.__table__.create(engine)
    with Session(engine) as s:
        s.add(Asset(id='BTC',symbol='BTC',known_at=1));s.flush()
        for quote in ('USDT','USDC'):
            s.add(Instrument(id='binance:perp:BTC'+quote,asset_id='BTC',venue='binance',native_symbol='BTC'+quote,market_type='linear_perpetual',quote_asset=quote,spec={'circulating_supply':'100','quality_received_at':1000}))
            ident=quote+'USD';payload={'symbol':ident,'index':'1'}
            s.add(DataCapability(key='binance:fx:'+ident,provider='binance',capability='fx',subject=ident,
                status='fresh',received_at=1000,observed_at=1000,expires_at=99999999,payload=payload))
        s.commit()
        yield engine,s
    engine.dispose()


def market(asset='BTC',quote='USDT',quality='fresh'):
    return NS(instrument_id='binance:perp:'+asset+quote,asset_id=asset,quote_asset=quote,price=D('10'),oi_notional_quote=D('50'),
        quality=NS(status=quality),oi_quality=NS(status='fresh'))


def test_batch_has_constant_queries_including_unknown_assets(db):
    engine,s=db; queries=[]
    def capture(*args):queries.append(1)
    event.listen(engine,'before_cursor_execute',capture)
    rows=[market() for _ in range(30)]+[market('UNKNOWN'+str(n)) for n in range(300)]
    inputs=valuation_inputs(rows,s)
    output=[valuation_for(m,s,1000,inputs) for m in rows]
    assert len(queries)==2
    assert output[0]['cap']==D('1000') and output[0]['ratio']==D('.05')
    assert all(v['status']=='binance_supply_not_provided' and v['ratio'] is None for v in output[30:])


@pytest.mark.parametrize('asset,quote,quality,now',[
    ('BTC','USDT','fresh',1000),('BTC','USDC','fresh',1000),
    ('BTC','USDT','stale',1000),('UNKNOWN','USDT','fresh',1000),
    ('BTC','USDT','fresh',4000000),('BTC','USDT','fresh',90000000),
])
def test_batch_preserves_freshness_missing_data_and_valuation_semantics(db,asset,quote,quality,now):
    _,s=db;m=market(asset,quote,quality)
    before=valuation_for(m,s,now)
    after=valuation_for(m,s,now,valuation_inputs([m],s))
    assert after==before
