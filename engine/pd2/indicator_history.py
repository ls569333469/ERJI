"""Database-only paged indicator series; frozen snapshots never borrow live rows."""
from hashlib import sha256
from functools import lru_cache
import json
import numpy as np
import talib
from sqlalchemy import select
from engine.models import Candle
from engine.pd2.analysis import DURATIONS, closed_bars, number


@lru_cache(maxsize=12)
def calculate(encoded, resolution):
    rows=json.loads(encoded);series=[]
    # Restart warmup after a real gap; never smooth across unavailable candles.
    segments=[]
    for bar in rows:
        if not segments or bar['open_time']!=segments[-1][-1]['close_time']:segments.append([])
        segments[-1].append(bar)
    for segment in segments:
        c,h,l=[np.array([float(b[k]) for b in segment]) for k in ('close','high','low')]
        macd,signal,hist=talib.MACD(c,12,26,9);u,m,lower=talib.BBANDS(c,20,2,2,0)
        values={'ema20':talib.EMA(c,20),'ema50':talib.EMA(c,50),'macd':macd,'signal':signal,'histogram':hist,
                'atr14':talib.ATR(h,l,c,14),'rsi14':talib.RSI(c,14),'adx14':talib.ADX(h,l,c,14),
                'bb_upper':u,'bb_middle':m,'bb_lower':lower}
        for i,b in enumerate(segment):
            series.append({'time':b['open_time'],'close_time':b['close_time'],'known_at':max(b['close_time'],b.get('received_at',0)),
                           **{k:number(v[i]) for k,v in values.items()}})
    return series


def page(session,instrument,resolution,before,limit,now,snapshot=None):
    if snapshot:
        assessment=next((a for a in snapshot.get('timeframe_assessments',[]) if a['timeframe']==resolution),{})
        source=assessment.get('series',[])
        revision=snapshot['series_revision'];status='historical_frozen'
        reason='仅返回该快照实际留存的指标；覆盖之外不使用后来补采的数据'
    else:
        from engine.pd2.offline_indicators import source_state,read as read_offline
        source_meta=source_state(session,instrument,resolution,now)
        if source_meta['count']>20000:
            return read_offline(session,instrument,resolution,before,limit,now,source_meta)
        # Stable first available candle is the seed for every page in this revision.
        # A cap is explicit instead of silently switching seeds on a very long market.
        rows=list(session.scalars(select(Candle).where(Candle.instrument_id==instrument,Candle.resolution==resolution,
            Candle.received_at<=now,Candle.close_time<=now,Candle.is_closed.is_(True)).order_by(Candle.open_time).limit(20001)))
        if len(rows)>20000:return {'instrument_id':instrument,'timeframe':resolution,'as_of':now,
            'snapshot_id':None,'series_revision':'coverage_limit','rows':[],'status':'coverage_limit',
            'reason':'历史超过当前单市场计算上限，需离线建立新序列版本','has_more':False}
        from engine.pd2.service import candle_dict
        bars=closed_bars([candle_dict(b) for b in rows],now)
        encoded=json.dumps(bars,sort_keys=True,separators=(',',':'))
        revision=sha256(encoded.encode()).hexdigest()
        source=calculate(encoded,resolution) if bars else []
        status='available' if source else 'missing_data';reason=None
    eligible=[p for p in source if before is None or p['time']<before]
    result=eligible[-limit:]
    return {'instrument_id':instrument,'timeframe':resolution,'as_of':snapshot['as_of'] if snapshot else now,
            'snapshot_id':snapshot.get('id') if snapshot else None,'series_revision':revision,
            'rows':result,'status':status,'reason':reason,'has_more':len(eligible)>limit,
            'coverage_start':source[0]['time'] if source else None,'coverage_end':source[-1]['close_time'] if source else None,
            'next_before':result[0]['time'] if result else None,'method':'talib_closed_segment_seed_v1'}
