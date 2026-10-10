"""Bounded native history repair using the existing candle writer and budget.

Budget-driven cold lane; persisted cursors survive restart. Reception time is
never rewritten to make retrospectively fetched candles known in the past.
"""
import asyncio
from sqlalchemy import select,func
from engine.db import Session
from engine.models import Instrument,Candle,UserPreference
from engine.adapters.base import now_ms
from engine.pd2.collection_schedule import due,acknowledge
from engine.pd2.universe_policy import primary_eligible
from engine.repository import save_batch

STEP=14400000
MAX_BARS=250000
STEPS={"60":3600000,"240":STEP,"1D":86400000}
INTERVALS={"60":"1h","240":"4h","1D":"1d"}
KINDS={"60":"candle_long_history","240":"candle_history","1D":"candle_daily_history"}

def advance(cursor, end, rows, step=STEP):
    closed={r['open_time']:r for r in rows if r['is_closed']}
    while cursor<end:
        row=closed.get(cursor)
        if not row or row['close_time']!=cursor+step:break
        cursor+=step
    return min(cursor,end)

def state_key(iid,resolution='240'):
    return 'p34_history:'+(resolution+':' if resolution!='240' else '')+iid


def target_for(resolution):
    step=STEPS[resolution]
    with Session() as s:
        instruments=list(s.scalars(select(Instrument).where(Instrument.venue=='binance')))
        earliest=dict(s.execute(select(Candle.instrument_id,func.min(Candle.open_time)).where(
            Candle.resolution==resolution,Candle.is_closed==True).group_by(Candle.instrument_id)).all())
        states={r.key:r.payload for r in s.scalars(select(UserPreference).where(
            UserPreference.key.startswith('p34_history:',autoescape=True)))}
    candidates={i.native_symbol:i for i in instruments if primary_eligible(i) and
        i.spec.get('source')=='exchangeInfo' and i.spec.get('onboard_time') and i.id in earliest
        and states.get(state_key(i.id,resolution),{}).get('status')!='coverage_limit'
        and states.get(state_key(i.id,resolution),{}).get('retry_after',0)<=now_ms()
        and (states.get(state_key(i.id,resolution),{}).get('status')!='complete' or
             now_ms()-states.get(state_key(i.id,resolution),{}).get('last_attempt_at',0)>=86400000)}
    if resolution=='60':
        # A bounded representative long series for paged indicators/AVWAP.
        # 4H alone cannot exercise >20k native bars on currently listed contracts.
        eligible=sorted((i for i in instruments if primary_eligible(i) and i.spec.get('onboard_time') and i.id in earliest),
                        key=lambda i:(i.spec['onboard_time'],i.id))[:1]
        candidates={i.native_symbol:i for i in eligible if i.native_symbol in candidates}
    kind=KINDS[resolution]
    for name in due('binance',list(candidates),kind,1):
        i=candidates[name];start=(i.spec['onboard_time']+step-1)//step*step;end=earliest[i.id]
        old=states.get(state_key(i.id,resolution),{})
        if old: return i,old
        if resolution=='60':start=max(start,end-22040*step)
        return i,{'start':start,'end':end,'next_open':start,'timeframe':resolution,
            'status':'coverage_limit' if (now_ms()-start)//step>MAX_BARS else 'pending',
            'max_bars':MAX_BARS,'scope':'bounded_long_indicator_history' if resolution=='60' else 'contract_lifetime',
            'source':'Binance exchangeInfo.onboardDate + native klines'}
    return None


def target():
    with Session() as s:
        row=s.get(UserPreference,'p34_history_lane')
        previous=(row.payload if row else {}).get('timeframe','60')
    lanes=('1D','240','60');offset=(lanes.index(previous)+1)%len(lanes) if previous in lanes else 0
    chosen=None
    for tf in lanes[offset:]+lanes[:offset]:
        chosen=target_for(tf)
        if chosen:break
    if chosen:
        with Session.begin() as s:s.merge(UserPreference(key='p34_history_lane',updated_at=now_ms(),
            payload={'timeframe':chosen[1]['timeframe']}))
    return chosen


def internal_gap(iid,resolution='240'):
    # Examine the full stored interval, not just the most recent 300 candles.
    with Session() as s:
        grid=select(Candle.close_time.label('closed'),func.lead(Candle.open_time).over(
            order_by=Candle.open_time).label('next_open')).where(Candle.instrument_id==iid,
            Candle.resolution==resolution,Candle.is_closed==True).subquery()
        return s.execute(select(grid.c.closed,grid.c.next_open).where(grid.c.next_open>grid.c.closed)
            .order_by(grid.c.closed).limit(1)).first()

def checkpoint(iid,state):
    with Session.begin() as s:s.merge(UserPreference(key=state_key(iid,state.get('timeframe','240')),updated_at=now_ms(),payload=state))


def stored_advance(iid, cursor, end, resolution):
    """Another repair may already cover the cursor; never stall on a shared hit."""
    step=STEPS[resolution];stop=min(end,cursor+1000*step)
    with Session() as s:
        rows=[dict(open_time=opened,close_time=closed,is_closed=True) for opened,closed in s.execute(
            select(Candle.open_time,Candle.close_time).where(Candle.instrument_id==iid,
                Candle.resolution==resolution,Candle.is_closed.is_(True),Candle.open_time>=cursor,
                Candle.open_time<stop,Candle.close_time<=now_ms(),Candle.received_at<=now_ms()))]
    return advance(cursor,stop,rows,step)

async def collect_one(adapter):
    chosen=await asyncio.to_thread(target)
    if not chosen:return {'status':'idle'}
    inst,state=chosen;state=dict(state)
    resolution=state.get('timeframe','240');step=STEPS[resolution]
    kind=KINDS[resolution]
    if state['status']=='coverage_limit':
        await asyncio.to_thread(checkpoint,inst.id,state)
        return {'status':'coverage_limit','instrument_id':inst.id}
    cursor,end=state['next_open'],state['end']
    if cursor>=end:
        gap=await asyncio.to_thread(internal_gap,inst.id,resolution)
        if gap:
            cursor,end=gap;state.update(next_open=cursor,end=end,status='gap')
        else:
            state.update(status='complete',last_attempt_at=now_ms())
            await asyncio.to_thread(checkpoint,inst.id,state)
            await asyncio.to_thread(acknowledge,'binance',[inst.native_symbol],kind)
            return {'status':'complete','instrument_id':inst.id,'bars':0}
    # Known end bounds limit overlap with current candles, never change OHLC.
    limit=min(1500,max(2,(end-cursor)//step))
    from engine.p38.candle_gaps import fetch
    batch=await fetch(adapter,(inst.id,inst.native_symbol,resolution,INTERVALS[resolution],limit,cursor),'history',archive=inst if getattr(adapter,'client',None) else None)
    covered=await asyncio.to_thread(stored_advance,inst.id,cursor,end,resolution)
    if batch is None:
        if covered>cursor:
            state.update(next_open=covered,last_attempt_at=now_ms(),status='check_internal_gaps' if covered>=end else 'progress')
            await asyncio.to_thread(checkpoint,inst.id,state)
        return {'status':'shared_coverage_progress' if covered>cursor else 'shared_gap_wait','instrument_id':inst.id}
    new_cursor=max(covered,advance(cursor,end,batch.candles,step))
    state.update(next_open=new_cursor,last_attempt_at=now_ms(),
        status='check_internal_gaps' if new_cursor>=end else 'progress' if new_cursor>cursor else 'gap',
        last_error=batch.errors[0] if batch.errors else None)
    if new_cursor==cursor:
        failures=state.get('stalled_attempts',0)+1
        state.update(stalled_attempts=failures,retry_after=now_ms()+min(86400000,3600000*2**min(failures-1,5)),
            missing_from=cursor,first_returned_open=min((r['open_time'] for r in batch.candles),default=None))
    else:
        state.update(stalled_attempts=0,retry_after=0,missing_from=None)
    await asyncio.to_thread(checkpoint,inst.id,state)
    # This page was attempted, not necessarily complete. Rotate fairly while
    # keeping the coverage cursor at the first missing bar.
    await asyncio.to_thread(acknowledge,'binance',[inst.native_symbol],kind)
    return {'status':state['status'],'instrument_id':inst.id,'page_bars':len(batch.candles),
            'next_open':new_cursor,'end':end,'errors':len(batch.errors)}
