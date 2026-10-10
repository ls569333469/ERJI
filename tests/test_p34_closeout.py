import asyncio
from types import SimpleNamespace as N
from decimal import Decimal as D
from engine.pd2 import native_streams,history_backfill,derivative_collection,oi_aggregate
from engine.pd2.derivatives import settled_costs
from engine.repository import merge_market_payload
from engine.models import Candle,DataCapability,OiPoint,UserPreference,RawEvent
from sqlalchemy import select
from test_p25_signals import isolated,seed,NOW


def test_quote_provenance_survives_late_rest_oi():
    value=merge_market_payload({'as_of':200,'price':'2','raw_ref':'ws','received_at':210},
        {'as_of':100,'price':'1','raw_ref':'rest','received_at':220,'oi_base':'99','oi_as_of':215})
    assert (value['price'],value['raw_ref'],value['received_at'])==('2','ws',210)
    assert value['oi_base']=='99'


def test_execution_retry_does_not_flood_unsupported_source_or_reuse_old_quote():
    from engine.pd2.execution import needs_refresh
    assert not needs_refresh({'status':'fresh','received_at':NOW-1000,'observed_at':NOW-2000},NOW)
    assert needs_refresh({'status':'fresh','received_at':NOW-1000,'observed_at':NOW-20000},NOW)
    assert not needs_refresh({'status':'source_time_unknown','last_attempt_at':NOW-20000},NOW)
    assert needs_refresh({'status':'source_time_unknown','last_attempt_at':NOW-300000},NOW)


def test_depth_coalescing_is_bounded_ordered_and_preserves_source_time(isolated,monkeypatch):
    seed(isolated);iid='binance:perp:P25TEST0USDT';native='P25TEST0USDT'
    monkeypatch.setattr(native_streams,'now_ms',lambda:NOW+5000)
    monkeypatch.setattr(native_streams,'packet_instruments',lambda *a:{native:iid})
    def packet(seq,at):return {'data':{'e':'depthUpdate','s':native,'u':seq,'E':at,'T':at,'b':[['1','20']],'a':[['2','20']]}}
    buffer=native_streams.LatestDepth([native])
    for n in range(1000):buffer.offer(packet(n,NOW+n),NOW+n+10)
    buffer.offer(packet(1,NOW),NOW+4000)
    buffer.offer({'data':{'e':'aggTrade','s':native}},NOW+4000)
    values=buffer.drain()
    assert len(values)==1 and values[0][0]['u']==999 and values[0][1]==NOW+1009
    native_streams.save_depth_batch('binance',values)
    with isolated() as s:
        record=s.get(DataCapability,'binance:execution_book:'+iid)
        assert record.observed_at==NOW+999 and record.received_at==NOW+1009
        assert record.payload['sequence']==999
    # An old packet with a higher sequence must not rejuvenate a stale book.
    native_streams.save_depth_batch('binance',[(packet(2000,NOW-20000)['data'],NOW+5000)])
    with isolated() as s:assert s.get(DataCapability,'binance:execution_book:'+iid).payload['sequence']==999


def test_ws_4h_candle_is_not_written_into_hourly_series(isolated,monkeypatch):
    seed(isolated);iid='binance:perp:P25TEST0USDT';native='P25TEST0USDT'
    monkeypatch.setattr(native_streams,'packet_instruments',lambda *a:{native:iid})
    start=NOW//14400000*14400000-14400000
    packet={'data':{'e':'kline','s':native,'k':{'i':'4h','t':start,'T':start+14400000-1,'x':True,
        'o':'1','h':'2','l':'.5','c':'1.5','v':'20','q':'25'}}}
    native_streams.save_packet('binance',packet,NOW)
    with isolated() as s:
        assert s.scalar(select(Candle).where(Candle.instrument_id==iid,Candle.resolution=='240')).close_time==start+14400000
        assert s.scalar(select(Candle).where(Candle.instrument_id==iid,Candle.resolution=='60')) is None


def test_funding_millisecond_jitter_does_not_create_fake_interval_or_gap():
    h=3600000;end=1000*h
    records=[{'fundingTime':end-i*4*h+(1 if i%2 else 0),'fundingRate':'.001'} for i in range(8)]
    points=derivative_collection.funding_points(records,'test',end+100,'raw')
    assert all(p['interval_seconds']==14400 for p in points[1:])
    rows=[{'time':p['settled_at'],'rate':p['rate'],'interval_seconds':p['interval_seconds']} for p in points]
    result=settled_costs(rows,end+100,end+100)
    assert result[2]['status']=='available' and result[2]['settlement_count']==6
    assert settled_costs(rows[:3]+rows[4:],end+100,end+100)[2]['status']=='gap'
    assert settled_costs(rows,end+3600000,end+100)[2]['status']=='tail_unverified'


def test_history_gap_is_retained_backed_off_and_yields_queue(monkeypatch):
    from engine.p38 import candle_gaps
    async def fetch(adapter,target,lane,archive=None):return await adapter.collect_candles([target])
    monkeypatch.setattr(candle_gaps,"fetch",fetch)
    step=history_backfill.STEP;start=100*step;inst=N(id='x',native_symbol='X')
    state={'start':start,'next_open':start,'end':start+10*step,'status':'pending'};saved=[];acks=[]
    monkeypatch.setattr(history_backfill,'target',lambda:(inst,state))
    monkeypatch.setattr(history_backfill,'checkpoint',lambda i,s:saved.append(s))
    monkeypatch.setattr(history_backfill,'save_batch',lambda b:None)
    monkeypatch.setattr(history_backfill,'acknowledge',lambda *a:acks.append(a))
    monkeypatch.setattr(history_backfill,'now_ms',lambda:NOW)
    class Adapter:
        async def collect_candles(self,targets):
            return N(candles=[{'open_time':start+step,'close_time':start+2*step,'is_closed':True}],errors=[])
    result=asyncio.run(history_backfill.collect_one(Adapter()))
    assert result['next_open']==start and saved[0]['status']=='gap'
    assert saved[0]['retry_after']>NOW and saved[0]['first_returned_open']==start+step
    assert acks==[('binance',['X'],'candle_history')]


def test_recent_oi_grid_repairs_same_market_quantity_without_fake_usd_fx(isolated):
    seed(isolated);iid='binance:perp:P25TEST0USDT';end=NOW//300000*300000
    with isolated.begin() as s:
        s.add(RawEvent(id='p34-oi-test-source',source='test',endpoint='test',received_at=NOW,payload={}));s.flush()
        s.add(OiPoint(instrument_id=iid,event_time=end-3600000,base_quantity=D('100'),
            quote_asset='USDT',notional_quote=D('200'),mark_price=D('2'),received_at=NOW,raw_ref='p34-oi-test-source'))
    current={'markets':[{'market_key':iid,'instrument_id':iid,'source':'native','quantity':'120','observed_at':end}]}
    with isolated() as s:
        result=oi_aggregate.native_quantity_change(s,current,3600000,NOW)
        assert D(result['quantity_change_pct'])==20
        assert 'notional_change_pct' not in result
        assert result['quantity_endpoints'][0]['start_at']==end-3600000
        assert oi_aggregate.native_quantity_change(s,current,14400000,NOW) is None
        current['markets'].append({'source':'cmc','quantity':None})
        assert oi_aggregate.native_quantity_change(s,current,3600000,NOW) is None


def test_oi_seed_and_recent_requests_share_one_turn(isolated):
    inst=N(id='p34-seed-test')
    assert derivative_collection.history_request(inst,NOW)[0]==('1h',73)
    with isolated.begin() as s:s.add(UserPreference(key='p34_oi_coverage:'+inst.id,updated_at=NOW,
        payload={'hourly_seed_at':NOW,'hourly_seed_complete':False}))
    assert derivative_collection.history_request(inst,NOW+300000)[0]==('5m',500)
    assert derivative_collection.history_request(inst,NOW+21600000)[0]==('1h',73)


def test_release_counts_do_not_double_count_horizons_or_asset_aliases():
    from engine.strategies.p33_release import sample_counts
    row={'event_id':'one','strategy_id':'recent_high','status':'completed_cost_scenario',
         'canonical_asset_id':'one-asset','identity_artifact':'identity','source_artifact':'scenario',
         'entry_bar_open':NOW-4*86400000,'exit_bar_close':NOW-3*86400000,'mode':'historical_replay'}
    result=sample_counts([{**row,'horizon_hours':24},{**row,'horizon_hours':72},
        {**row,'event_id':'two','entry_bar_open':NOW-2*86400000,'exit_bar_close':NOW-86400000}],
        'recent_high',NOW)
    assert result['mature_fills']==2 and result['assets']==1
    assert result['historical_days']==2 and result['forward_days']==0
    assert sample_counts([{**row,'status':'right_censored_history'}],'recent_high',NOW)['mature_fills']==0


def test_release_requires_retained_counts_and_frozen_day_basis(isolated):
    import json
    from hashlib import sha256
    import pytest
    from engine.models import SnapshotDataset
    from engine.strategies.p33_release import verified_sample
    def add(session,payload,at=NOW-1000000000):
        key=sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        session.merge(SnapshotDataset(id=key,created_at=at,payload=payload));session.flush();return key
    with isolated.begin() as s:
        identity=add(s,{'canonical_asset_id':'one','status':'verified'})
        row={'event_id':'one-event','strategy_id':'recent_high','status':'completed_cost_scenario','horizon_hours':24,
            'entry_bar_open':NOW-2*86400000,'exit_bar_close':NOW-86400000}
        source=add(s,row)
        sample=add(s,{'records':[{**row,'mode':'historical_replay','canonical_asset_id':'one',
            'identity_artifact':identity,'source_artifact':source}]})
        protocol=add(s,{'sample_days_basis':'historical_days'})
        report={'sample_artifact':sample,'protocol_artifact':protocol,'protocol_registered_at':NOW-900000000,
            'sample':{'mature_fills':1,'assets':1,'days':1}}
        assert verified_sample(s,report,'recent_high',NOW)['forward_days']==0
        with pytest.raises(ValueError,match='declared_sample_counts_mismatch'):
            verified_sample(s,{**report,'sample':{'mature_fills':2,'assets':1,'days':1}},'recent_high',NOW)
        s.get(SnapshotDataset,source).payload={**row,'entry_bar_open':NOW-10*86400000};s.flush()
        with pytest.raises(ValueError,match='sample_evidence_missing_or_changed'):
            verified_sample(s,report,'recent_high',NOW)
