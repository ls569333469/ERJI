"""ATH is only an exchange contract lifetime high with complete native history."""
from decimal import Decimal as D
from hashlib import sha256
import json
from sqlalchemy import select,func,case,or_
from engine.models import Candle


def proof(rows, market, listed_at, cutoff):
    if not isinstance(listed_at,int) or listed_at<=0 or listed_at>=cutoff:return None
    start=listed_at//14400000*14400000
    rows=sorted((b for b in rows if b['is_closed'] and start<=b['open_time']<cutoff
                 and max(b['close_time'],b.get('received_at',0))<=cutoff),key=lambda b:b['open_time'])
    if not rows or rows[0]['open_time']!=start or rows[-1]['close_time']!=cutoff:return None
    if any(b['close_time']-b['open_time']!=14400000 for b in rows):return None
    if any(a['close_time']!=b['open_time'] for a,b in zip(rows,rows[1:])):return None
    return {'market_id':market,'complete':True,'scope':'contract_since_listing','start_time':start,'end_time':cutoff,
            'as_of':max(max(b['close_time'],b.get('received_at',0)) for b in rows),
            'high':str(max(D(b['high']) for b in rows)),
            'source_revision':sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()}


def load(session,instrument,cutoff):
    if instrument.id.split(':')[0]!='binance' or instrument.spec.get('source')!='exchangeInfo':return None
    listed=instrument.spec.get('onboard_time')
    step=14400000
    if not isinstance(listed,int) or isinstance(listed,bool) or listed<=0 or listed>=cutoff or cutoff%step:return None
    start=listed//step*step
    # Unique (market, timeframe, open_time) + exact grid membership + expected
    # count proves continuity without loading every historic OHLC row into Python.
    bad=or_(Candle.close_time-Candle.open_time!=step,Candle.open_time%step!=0)
    count,first,last,received,high,revisions,invalid=session.execute(select(
        func.count(),func.min(Candle.open_time),func.max(Candle.close_time),func.max(Candle.received_at),
        func.max(Candle.high),func.sum(Candle.revision),func.sum(case((bad,1),else_=0))).where(
        Candle.instrument_id==instrument.id,Candle.resolution=='240',Candle.open_time>=start,Candle.open_time<cutoff,
        Candle.close_time<=cutoff,Candle.received_at<=cutoff,Candle.is_closed.is_(True))).one()
    if count!=(cutoff-start)//step or first!=start or last!=cutoff or invalid:return None
    metadata=[instrument.id,start,cutoff,count,received,str(high),revisions]
    return {'market_id':instrument.id,'complete':True,'scope':'contract_since_listing','start_time':start,'end_time':cutoff,
        'as_of':max(last,received),'high':str(high),'source_revision':sha256(json.dumps(metadata).encode()).hexdigest(),
        'proof_method':'unique_complete_4h_grid_v2','bar_count':count}
