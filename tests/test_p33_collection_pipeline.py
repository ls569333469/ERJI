import asyncio
from decimal import Decimal
from types import SimpleNamespace as N
from unittest.mock import Mock

from engine.adapters.base import Batch
from engine.pd2 import collection_schedule, derivatives, native_streams
from engine.models import Asset, Instrument, MarketState
from engine.p38 import candle_gaps
from services.worker import main
from test_p25_signals import isolated


def test_ready_timeframe_saved_while_another_request_is_waiting(monkeypatch):
    async def fetch(adapter,target,lane):
        batch=await adapter.collect_candles([target]);main.save_batch(batch);return batch
    monkeypatch.setattr(candle_gaps,"fetch",fetch)
    saved=[];acks=[]
    monkeypatch.setattr('engine.repository.candle_window_ready',lambda iid,res:res in saved)
    monkeypatch.setattr(main,'save_batch',lambda batch:saved.extend(r['resolution'] for r in batch.candles))
    monkeypatch.setattr(main,'heartbeat',Mock())
    monkeypatch.setattr(collection_schedule,'acknowledge',lambda *a:acks.append((a,list(saved))))
    async def scenario():
        waiting=asyncio.Event();release=asyncio.Event()
        async def collect(targets):
            res=targets[0][2]
            if res=='240':
                waiting.set();await release.wait()
            return Batch('binance',candle_only=True,candles=[{'instrument_id':'test:A','resolution':res}])
        task=asyncio.create_task(main.persist_candle_targets(N(venue='binance',collect_candles=collect),[
            ('test:A','A','60','1h',2),('test:A','A','240','4h',2)]))
        await waiting.wait()
        for _ in range(100):
            if saved:break
            await asyncio.sleep(.01)
        assert saved==['60'] and not acks and not task.done()
        release.set();await task
    asyncio.run(scenario())
    assert acks==[(('binance',['A'],'candles'),['60','240'])]


def test_partial_market_does_not_advance_deadline_or_block_other_market(monkeypatch):
    async def fetch(adapter,target,lane):
        batch=await adapter.collect_candles([target]);main.save_batch(batch);return batch
    monkeypatch.setattr(candle_gaps,"fetch",fetch)
    acks=[];health=[]
    monkeypatch.setattr('engine.repository.candle_window_ready',lambda iid,res:not (iid=='test:A' and res=='240'))
    monkeypatch.setattr(main,'save_batch',Mock())
    monkeypatch.setattr(main,'heartbeat',lambda *a:health.append(a))
    monkeypatch.setattr(collection_schedule,'acknowledge',lambda *a:acks.extend(a[1]))
    async def collect(targets):
        iid,native,res,*_=targets[0]
        if native=='A' and res=='240':return Batch('binance',candle_only=True,errors=['timeout'])
        return Batch('binance',candle_only=True,candles=[{'instrument_id':iid,'resolution':res}])
    asyncio.run(main.persist_candle_targets(N(venue='binance',collect_candles=collect),[
        ('test:A','A','60','1h',2),('test:A','A','240','4h',2),('test:B','B','60','1h',2)]))
    assert acks==['B']
    assert health[-1][1]=='partial' and health[-1][2]['targets_completed']==3


def test_compact_context_keeps_oi_and_funding_without_loading_tape(isolated,monkeypatch):
    now=2000000000000;iid='p33:pipeline:market';asset='P33PIPELINE'
    with isolated.begin() as s:
        s.add(Asset(id=asset,symbol=asset,known_at=now));s.flush()
        s.add(Instrument(id=iid,asset_id=asset,venue='binance',native_symbol=asset+'USD',market_type='linear_perpetual',quote_asset='USD',spec={}));s.flush()
        s.add(MarketState(instrument_id=iid,as_of=now,payload={'oi_status':'fresh','oi_as_of':now,'oi_notional_quote':'50','oi_base':'25','funding_rate':'.0001','funding_interval_seconds':28800}))
    def forbidden(*a,**kw):raise AssertionError('list must not load full tape evidence')
    monkeypatch.setattr(native_streams,'tape_context',forbidden)
    shared={'aliases':{asset:asset},'members':{asset:[asset]},'fx_rates':{'USD':Decimal(1)},'bykaranteli_whales':{},'liquidation_summaries':{}}
    market=N(asset_id=asset,instrument_id=iid,native_symbol=asset+'USD',quote_asset='USD',venue='binance',circulating_cap=Decimal(100))
    result=derivatives.context_for(market,now,shared,True)
    assert result['cross_market_oi']['total_oi_usd']=='50'
    assert result['cross_market_oi']['oi_cap_ratio']=='0.5'
    assert len(result['cross_market_oi']['changes'])==3
    assert result['funding_reference']['rate']=='.0001'
    assert 'native_tape' not in result


def test_binance_routes_preserve_subscriptions_but_split_market_and_depth():
    selected=[N(native_symbol='BTCUSDT'),N(native_symbol='ETHUSDT')]
    route,global_streams=native_streams.stream_route('binance',[],channel='global')
    assert route=='/market/stream' and global_streams==['!markPrice@arr@1s','!forceOrder@arr']
    market,streams=native_streams.stream_route('binance',selected,channel='market')
    depth,books=native_streams.stream_route('binance',selected,channel='depth')
    assert market=='/market/stream' and depth=='/public/stream'
    assert set(streams+books)=={symbol+suffix for symbol in ('btcusdt','ethusdt') for suffix in ('@aggTrade','@kline_1h','@kline_4h','@kline_1d','@kline_1w','@depth20@100ms')}
    assert not any('markPrice' in x for x in streams+books)
    assert native_streams.stream_route('binance_spot',selected)[0]=='/stream'
    assert native_streams.stream_route('binance_spot',[],channel='global')==('/stream',['!miniTicker@arr'])
    assert '!miniTicker@arr' not in native_streams.stream_route('binance_spot',selected,channel='market')[1]


def test_global_quotes_keep_oi_and_reject_older_event_times(isolated,monkeypatch):
    iid='p33:pipeline:quote';asset='P33QUOTE';now=2000000000000
    with isolated.begin() as s:
        s.add(Asset(id=asset,symbol=asset,known_at=now));s.flush()
        s.add(Instrument(id=iid,asset_id=asset,venue='binance',native_symbol=asset+'USDT',market_type='linear_perpetual',quote_asset='USDT',spec={}));s.flush()
        s.add(MarketState(instrument_id=iid,as_of=now,payload={'price':'1','oi_base':'50','oi_as_of':now-1000}))
    monkeypatch.setattr(native_streams,'packet_instruments',lambda *a:{asset+'USDT':iid})
    monkeypatch.setattr(native_streams,'now_ms',lambda:now+1500)
    def packet(at,price):return {'stream':'!markPrice@arr@1s','data':[{'e':'markPriceUpdate','s':asset+'USDT','E':at,'p':price}]}
    native_streams.save_packet('binance',packet(now+1000,'2'),now+1300)
    native_streams.save_packet('binance',packet(now-1000,'9'),now+1500)
    with isolated() as s:
        state=s.get(MarketState,iid)
        assert state.as_of==now+1000 and state.payload['price']=='2'
        assert state.payload['oi_base']=='50' and state.payload['oi_as_of']==now-1000
