from copy import deepcopy
from decimal import Decimal as D
from sqlalchemy import select
from test_p25_signals import isolated
from engine.models import Asset,Instrument,Candle,RawEvent,SnapshotDataset
from engine.pd2.persistent_avwap import update,read_series

STEP=14400000
ANCHOR=1700006400000
NOW=ANCHOR+2000*STEP
IID='binance:perp:P33AVWAPAUDITUSDT'


def seed(session,count=1800):
    session.add(Asset(id='P33AVWAPAUDIT',symbol='P33AVWAPAUDIT',known_at=ANCHOR));session.flush()
    session.add(Instrument(id=IID,asset_id='P33AVWAPAUDIT',venue='binance',native_symbol='P33AVWAPAUDITUSDT',market_type='linear_perpetual',quote_asset='USDT',spec={}));session.flush()
    session.add(RawEvent(id='p33-avwap-audit-source',source='test',endpoint='test',received_at=ANCHOR,payload={}));session.flush()
    for i in range(count):
        session.add(Candle(instrument_id=IID,resolution='240',open_time=ANCHOR+i*STEP,close_time=ANCHOR+(i+1)*STEP,
            is_closed=True,open='10',high='11',low='9',close='10',base_volume='1',quote_volume='10',
            received_at=ANCHOR+(i+1)*STEP,raw_ref='p33-avwap-audit-source'))
    session.flush()


def test_revision_older_than_400_bars_rebuilds_without_mutating_frozen_generation(isolated):
    with isolated.begin() as s:
        seed(s)
        first=update(s,'event',IID,'240',ANCHOR,NOW)
        before=update(s,'event',IID,'240',ANCHOR,NOW+60001)
        assert len(read_series(s,before))==1800
        frozen=deepcopy(before)
        old_dataset=s.get(SnapshotDataset,before['segments'][0]['id']);original=deepcopy(old_dataset.payload)
        candle=s.get(Candle,(IID,'240',ANCHOR+10*STEP))
        candle.high=D('14');candle.revision+=1;candle.received_at=NOW+60002;s.flush()
        rebuilding=update(s,'event',IID,'240',ANCHOR,NOW+120002)
        assert rebuilding['generation']==2 and rebuilding['status']=='rebuilding'
        assert read_series(s,rebuilding)==[]
        assert old_dataset.payload==original
        after=update(s,'event',IID,'240',ANCHOR,NOW+180003)
        assert after['status']=='available'
        assert D(read_series(s,after)[-1]['event_avwap'])==D('18001')/D('1800')
        assert D(read_series(s,frozen)[-1]['event_avwap'])==D('10')
        assert after['supersedes']


def test_missing_old_candle_blocks_rebuild_instead_of_joining_across_gap(isolated):
    with isolated.begin() as s:
        seed(s)
        update(s,'gap-event',IID,'240',ANCHOR,NOW)
        before=update(s,'gap-event',IID,'240',ANCHOR,NOW+60001)
        s.delete(s.get(Candle,(IID,'240',ANCHOR+10*STEP)));s.flush()
        state=update(s,'gap-event',IID,'240',ANCHOR,NOW+120002)
        assert state['status']=='interval_gap' and state['next_open']==ANCHOR+10*STEP
        assert read_series(s,state)==[]
        assert len(read_series(s,before))==1800
