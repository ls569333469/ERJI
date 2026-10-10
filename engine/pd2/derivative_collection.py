"""Durable slow derivative tasks, separate from current quantity OI publication."""
import asyncio
from decimal import Decimal as D
from sqlalchemy import select
from engine.adapters.base import Batch, decimal_text, now_ms
from engine.db import Session
from engine.models import Instrument, UserPreference
from engine.pd2.collection_schedule import due, acknowledge
from engine.repository import save_batch

MARK_SKEW_MS = 60000

def notional_evidence(batch, item, price_ref, price_time):
    """Archive both native inputs alongside the derived point, not just in latest state."""
    from hashlib import sha256
    import json
    payload={'formula':'native_quantity_times_mark_v1','quantity':item['base_quantity'],
        'multiplier':'1','quote_asset':item['quote_asset'],'price':item['mark_price'],
        'oi_time':item['event_time'],'price_time':price_time,'max_skew_ms':MARK_SKEW_MS,
        'oi_raw_ref':item['raw_ref'],'price_raw_ref':price_ref,'value':item['notional_quote']}
    ref=sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    batch.raw.append({'id':ref,'source':'erji','endpoint':'derived_oi_notional_v1',
                      'received_at':item['received_at'],'payload':payload})
    item['raw_ref']=ref

def aligned_notional(quantity, price, oi_time, mark_time):
    if not oi_time or not mark_time or abs(oi_time-mark_time)>MARK_SKEW_MS:return None
    quantity,price=D(str(quantity)),D(str(price))
    if not quantity.is_finite() or not price.is_finite() or quantity<0 or price<=0:return None
    return decimal_text(quantity*price)

def history_points(records, instrument, received, ref):
    points={}
    for row in records:
        q,n,at=D(str(row['sumOpenInterest'])),D(str(row['sumOpenInterestValue'])),int(row['timestamp'])
        if not q.is_finite() or not n.is_finite() or q<=0 or n<0 or at>received:continue
        points[at]=dict(instrument_id=instrument.id,event_time=at,base_quantity=decimal_text(q),
            notional_quote=decimal_text(n),quote_asset=instrument.quote_asset,
            mark_price=decimal_text(n/q),received_at=received,raw_ref=ref)
    return [points[k] for k in sorted(points)]

def coverage(points, now, onboard=0):
    step=3600000;end=now//step*step
    start=max(end-72*step,((onboard+step-1)//step)*step if onboard else 0)
    expected=set(range(start,end+1,step));seen={p['event_time'] for p in points}
    missing=sorted(expected-seen)
    return {'start':start,'end':end,'expected':len(expected),'present':len(expected&seen),
            'missing':missing,'complete':bool(expected) and not missing}

def funding_points(records, instrument_id, received, ref):
    records=sorted((r for r in records if int(r['fundingTime'])<=received),key=lambda r:int(r['fundingTime']))
    result=[]
    for index,row in enumerate(records):
        # Exchanges can stamp settlements a few milliseconds after the boundary.
        # Keep the original timestamp but do not turn 4h into 14399 seconds.
        interval=round((int(row['fundingTime'])-int(records[index-1]['fundingTime']))/1000) if index else None
        result.append(dict(instrument_id=instrument_id,settled_at=int(row['fundingTime']),
                           rate=decimal_text(row['fundingRate']),interval_seconds=interval,raw_ref=ref))
    return result

def inputs():
    with Session() as s:
        return list(s.scalars(select(Instrument).where(Instrument.venue=='binance',
            Instrument.market_type=='linear_perpetual')))

def history_request(inst, now):
    with Session() as s:
        record=s.get(UserPreference,'p34_oi_coverage:'+inst.id)
        previous=dict(record.payload) if record else {}
    # Keep the 72h UTC seed, then repair rolling endpoints on a 5m grid. Only
    # one request per scheduled turn; no second request added to the budget.
    hourly=not previous.get('hourly_seed_at') or now-previous['hourly_seed_at']>=21600000
    return ('1h',73) if hourly else ('5m',500),previous

def record_coverage(inst, result, received):
    with Session.begin() as s:
        s.merge(UserPreference(key='p34_oi_coverage:'+inst.id,updated_at=received,payload=result))

async def collect_due(adapter, kinds=('funding_history','oi_history'), continuous=False):
    instruments=await asyncio.to_thread(inputs)
    instruments={i.native_symbol:i for i in instruments if i.spec.get('status') not in
                 ('CLOSED','DELISTED','inactive','delisted','delisted_or_ineligible')}
    result={'oi_history':0,'funding_history':0,'incomplete_history':0,'errors':[]}
    from engine.pd2.request_budget import request_lane
    async def collect_kind(kind):
        chosen=[] if continuous else await asyncio.to_thread(due,'binance',list(instruments),kind,24,240000)
        slots=asyncio.Semaphore(4 if kind=='oi_history' else 3)
        async def one(native):
            async with slots:
                token=request_lane.set('repair')
                try:await process(native,kind)
                finally:request_lane.reset(token)
        if continuous:
            from engine.pd2.continuous_collection import dispatch
            from engine.repository import runtime_config
            started=now_ms()
            async def choose(capacity):return await asyncio.to_thread(due,'binance',list(instruments),kind,capacity,240000)
            last_health=0
            async def enabled():
                nonlocal last_health
                if now_ms()-last_health>=5000:
                    from engine.repository import heartbeat
                    await asyncio.to_thread(heartbeat,'derivative_'+kind,'partial' if result['errors'] else 'ok',dict(result))
                    last_health=now_ms()
                return (await asyncio.to_thread(runtime_config)).collector_enabled and now_ms()-started<1800000
            await dispatch(choose,one,enabled,4 if kind=='oi_history' else 3)
        else:await asyncio.gather(*(one(native) for native in chosen))
    async def process(native,kind):
        inst=instruments[native];batch=Batch('binance',candle_only=True)
        try:
            if kind=='funding_history':
                records,ref,received=await adapter.request(batch,'GET',adapter.base+'/fundingRate',
                    params={'symbol':native,'limit':26})
                batch.funding=funding_points(records,inst.id,received,ref)
                if not batch.funding:raise ValueError('empty_funding_history')
                last=batch.funding[-1]
                batch.states=[dict(instrument_id=inst.id,as_of=0,funding_rate=last['rate'],
                    funding_interval_seconds=last['interval_seconds'],funding_settled_at=last['settled_at'],
                    funding_status='fresh',funding_kind='settled',funding_observed_through=received)]
                await asyncio.to_thread(save_batch,batch)
            else:
                (period,limit),previous=await asyncio.to_thread(history_request,inst,now_ms())
                records,ref,received=await adapter.request(batch,'GET',
                    'https://fapi.binance.com/futures/data/openInterestHist',
                    params={'symbol':native,'period':period,'limit':limit})
                batch.oi=history_points(records,inst,received,ref)
                evidence=coverage(batch.oi,received,inst.spec.get('onboard_time') or 0) if period=='1h' else recent_coverage(batch.oi,received,inst.spec.get('onboard_time') or 0)
                evidence.update(period=period,hourly_seed_at=received if period=='1h' else previous.get('hourly_seed_at'),
                    hourly_seed_complete=evidence['complete'] if period=='1h' else previous.get('hourly_seed_complete',False),raw_ref=ref)
                if not batch.oi:raise ValueError('empty_oi_history')
                await asyncio.to_thread(save_batch,batch)
                await asyncio.to_thread(record_coverage,inst,evidence,received)
                if not evidence['complete']:
                    result['incomplete_history']+=1
                    # A partial response is recorded as partial, but cannot
                    # monopolize the queue. Retry on the next regular turn.
            await asyncio.to_thread(acknowledge,'binance',[native],kind,received)
            result[kind]+=1
        except Exception as error:
            result['errors'].append(native+':'+kind+':'+type(error).__name__)
            result['errors'][:]=result['errors'][-50:]
    await asyncio.gather(*(collect_kind(kind) for kind in kinds))
    return result


def recent_coverage(points, now, onboard=0):
    step=300000;end=now//step*step
    start=max(end-86400000,((onboard+step-1)//step)*step if onboard else 0)
    expected=set(range(start,end+1,step));seen={p['event_time'] for p in points}
    missing=sorted(expected-seen)
    return {'start':start,'end':end,'expected':len(expected),'present':len(expected&seen),
            'missing':missing,'complete':bool(expected) and not missing}
