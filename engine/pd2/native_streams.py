"""Read-only native WebSocket facts; liquidation feeds are explicitly sampled."""
import asyncio,json,hashlib,ssl
from decimal import Decimal as D
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from engine.db import Session
from engine.models import Instrument,MarketState,RawEvent,UserPreference,Campaign,Episode,DataCapability
from engine.adapters.base import now_ms
from engine.repository import emit,heartbeat

def force_event(venue,data):
    order=data.get('o') or {};qty=order.get('l');price=order.get('ap') or order.get('p')
    if qty is None or price is None or D(str(qty))<=0 or D(str(price))<=0:return None
    if order.get('S') not in ('BUY','SELL'):return None
    # l is last filled quantity; q is the requested order and z is cumulative fill.
    key=':'.join(str(order.get(k,'')) for k in ('s','T','S','l','ap'))
    return {'id':hashlib.sha256((venue+':force:'+key).encode()).hexdigest(),
      'instrument_id':venue+':perp:'+order['s'],'event_time':int(order['T']),
      'price':str(price),'quantity':str(qty),'notional_quote':str(D(str(price))*D(str(qty))),
      'liquidated_side':'short' if order['S']=='BUY' else 'long','kind':'liquidation',
      'scope':'native_1000ms_sample','source':venue,'original_quantity':str(order.get('q')),
      'cumulative_quantity':str(order.get('z')),'order_side':order['S']}

def trade_event(venue,data):
    if data.get('a') is None:return None
    quantity,price=D(str(data['q'])),D(str(data['p']))
    if quantity<=0 or price<=0:return None
    return {'id':hashlib.sha256((venue+':trade:'+data['s']+':'+str(data['a'])).encode()).hexdigest(),
      'instrument_id':venue+(':spot:' if venue=='binance_spot' else ':perp:')+data['s'],
      'event_time':int(data['T']),'price':str(price),'quantity':str(quantity),'notional_quote':str(price*quantity),
      'aggressor_side':'sell' if data['m'] else 'buy','kind':'trade','scope':'subscribed_native_aggregate_trades','source':venue}

def save_packet(venue,packet,received):
    data=packet.get('data',packet) if isinstance(packet,dict) else packet
    entries=data if isinstance(data,list) else [data]
    with Session.begin() as s:
        instruments={i.native_symbol:i.id for i in s.scalars(select(Instrument).where(Instrument.venue==venue))}
        updated=[]
        for row in entries:
            kind=row.get('e');native=row.get('s');iid=instruments.get(native)
            if kind=='markPriceUpdate' and iid:
                state=s.get(MarketState,iid)
                if not state or int(row['E'])<state.as_of:continue
                state.as_of=int(row['E']);state.payload={**state.payload,'price':str(row['p']),'price_kind':'mark','as_of':int(row['E']),'received_at':received,'index_price':row.get('i'),'predicted_funding_rate':row.get('r'),'next_funding_time':row.get('T')};updated.append(iid)
            elif kind in ('forceOrder','aggTrade'):
                fact=force_event(venue,row) if kind=='forceOrder' else trade_event(venue,row)
                if not fact or fact['instrument_id'] not in instruments.values():continue
                fact['received_at']=received
                s.execute(insert(RawEvent).values(id=fact['id'],source=venue,endpoint='ws:'+fact['kind'],received_at=received,payload=fact).on_conflict_do_nothing(index_elements=['id']))
        if updated:emit(s,'market_update',{'source':venue,'instruments':updated,'transport':'websocket'})

def stream_scope(venue,connected,instruments,at):
    with Session.begin() as s:
        key='ws_coverage:'+venue;r=s.get(UserPreference,key);history=(r.payload if r else {}).get('segments',[])
        if history and history[-1].get('end') is None:history[-1]['end']=at
        if connected:history.append({'start':at,'end':None,'instruments':instruments})
        s.merge(UserPreference(key=key,updated_at=at,payload={'connected':connected,'segments':history[-200:]}))

def subscriptions(venue):
    with Session() as s:
        rows=list(s.scalars(select(Instrument).join(Campaign,Campaign.reference_instrument_id==Instrument.id).join(Episode).where(Instrument.venue==venue,Episode.phase.notin_(['invalidated','ended']))))
        if venue=='binance_spot':
            active_assets=set(s.scalars(select(Campaign.asset_id).join(Episode).where(Episode.phase.notin_(['invalidated','ended']))))
            rows=list(s.scalars(select(Instrument).where(Instrument.venue==venue,Instrument.asset_id.in_(active_assets),Instrument.quote_asset=='USDT')))
        return list({i.id:i for i in rows}.values())

async def stream_venue(venue,stop_after=None):
    from websockets.asyncio.client import connect
    base={'binance':'wss://fstream.binance.com','aster':'wss://fstream.asterdex.com','binance_spot':'wss://stream.binance.com:9443'}[venue]
    backoff=1;started=now_ms()
    while stop_after is None or now_ms()-started<stop_after*1000:
        selected=await asyncio.to_thread(subscriptions,venue)
        streams=(['!markPrice@arr@1s','!forceOrder@arr'] if venue!='binance_spot' else [])+[i.native_symbol.lower()+'@aggTrade' for i in selected]
        if not streams:
            await asyncio.sleep(5);continue
        try:
            async with connect(base+'/stream?streams='+('/'.join(streams)),ssl=ssl.create_default_context(),open_timeout=15,ping_interval=20,ping_timeout=20,max_size=4*1024*1024) as ws:
                connected=now_ms();await asyncio.to_thread(stream_scope,venue,True,[i.id for i in selected],connected)
                backoff=1;last_mark=0
                while now_ms()-connected<60000:
                    if stop_after is not None and now_ms()-started>=stop_after*1000:return
                    raw=await asyncio.wait_for(ws.recv(),timeout=min(20,max(.1,stop_after-(now_ms()-started)/1000)) if stop_after is not None else 20);packet=json.loads(raw);at=now_ms()
                    # Whole-market marks are persisted at most once per two seconds.
                    if 'markPrice' in packet.get('stream',''):
                        if at-last_mark<2000:continue
                        last_mark=at
                    await asyncio.to_thread(save_packet,venue,packet,at)
                    await asyncio.to_thread(heartbeat,'stream_'+venue,'connected',{'trade_markets':len(selected),'liquidations':'1000ms_sample' if venue!='binance_spot' else 'not_applicable'})
        except Exception as e:
            await asyncio.to_thread(heartbeat,'stream_'+venue,'reconnecting',{'reason':type(e).__name__})
            await asyncio.sleep(min(backoff,max(0,stop_after-(now_ms()-started)/1000)) if stop_after is not None else backoff);backoff=min(backoff*2,30)
        finally:await asyncio.to_thread(stream_scope,venue,False,[],now_ms())

async def run_streams():
    await asyncio.gather(*(stream_venue(v) for v in ('binance','aster','binance_spot')))

def tape_context(instrument,now):
    with Session() as s:
        inst=s.get(Instrument,instrument)
        if not inst:return {'status':'unknown_instrument'}
        raw=list(s.scalars(select(RawEvent).where(RawEvent.source==inst.venue,RawEvent.endpoint.in_(['ws:trade','ws:liquidation']),RawEvent.received_at>now-86400000,RawEvent.received_at<=now,RawEvent.payload['instrument_id'].as_string()==instrument)))
        facts=[r.payload for r in raw if r.payload.get('instrument_id')==instrument and r.payload.get('event_time',0)<=now]
        coverage=s.get(UserPreference,'ws_coverage:'+inst.venue);segments=(coverage.payload if coverage else {}).get('segments',[])
    results=[]
    for name,duration in [('1H',3600000),('4H',14400000),('1D',86400000)]:
        rows=[r for r in facts if now-duration<r['event_time']<=now];trades=[r for r in rows if r['kind']=='trade'];liqs=[r for r in rows if r['kind']=='liquidation']
        scoped=sorted((max(now-duration,r['start']),min(now,r.get('end') or now)) for r in segments if instrument in r.get('instruments',[]) and r['start']<=now and (r.get('end') or now)>=now-duration)
        cursor=now-duration
        for a,b in scoped:
            if a>cursor:break
            cursor=max(cursor,b)
        complete=cursor>=now and bool(scoped)
        buy=sum(D(r['notional_quote']) for r in trades if r['aggressor_side']=='buy');sell=sum(D(r['notional_quote']) for r in trades if r['aggressor_side']=='sell')
        results.append({'period':name,'quote_asset':inst.quote_asset,'trade_status':'complete_window' if complete else 'partial_window' if trades else 'not_collected','buy_quote':str(buy) if trades or complete else None,'sell_quote':str(sell) if trades or complete else None,'delta_quote':str(buy-sell) if trades or complete else None,'trade_count':len(trades),'liquidation_status':'sample_observed' if liqs else 'not_observed_not_zero','short_liquidation_quote':str(sum(D(r['notional_quote']) for r in liqs if r['liquidated_side']=='short')) if liqs else None,'long_liquidation_quote':str(sum(D(r['notional_quote']) for r in liqs if r['liquidated_side']=='long')) if liqs else None})
    return {'instrument_id':instrument,'windows':results,'liquidation_events':[r for r in facts if r['kind']=='liquidation'],'source':inst.venue,'scope':'single_native_market','liquidation_scope':'1000ms_sample_not_full_exchange'}
