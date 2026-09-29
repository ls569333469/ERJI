"""Read-only native WebSocket facts; liquidation feeds are explicitly sampled."""
import asyncio,json,hashlib,ssl
from decimal import Decimal as D
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from engine.db import Session
from engine.models import Instrument,MarketState,RawEvent,UserPreference,Campaign,Episode,DataCapability
from engine.adapters.base import now_ms
from engine.repository import emit,heartbeat

_packet_maps={}
def packet_instruments(session,venue,received):
    from engine.pd2.universe_policy import membership
    cached=_packet_maps.get(venue)
    if cached and 0<=received-cached[0]<10000:return cached[1]
    aliases,admitted=membership(session)
    result={i.native_symbol:i.id for i in session.scalars(select(Instrument).where(Instrument.venue==venue)) if aliases.get(i.asset_id,i.asset_id) in admitted}
    _packet_maps[venue]=(received,result)
    return result

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
        instruments=packet_instruments(s,venue,received)
        updated=[];candle_packets=[]
        for row in sorted(entries,key=lambda r:instruments.get(r.get('s'),'')):
            kind=row.get('e');native=row.get('s');iid=instruments.get(native)
            if kind=='markPriceUpdate' and iid:
                state=s.get(MarketState,iid,with_for_update=True)
                if not state or int(row['E'])<state.as_of:continue
                state.as_of=int(row['E']);state.payload={**state.payload,'price':str(row['p']),'price_kind':'mark','as_of':int(row['E']),'received_at':received,'index_price':row.get('i'),'predicted_funding_rate':row.get('r'),'next_funding_time':row.get('T')};updated.append(iid)
            elif kind=='24hrMiniTicker' and iid:
                state=s.get(MarketState,iid,with_for_update=True)
                if state and int(row['E'])>=state.as_of:
                    state.as_of=int(row['E']);state.payload={**state.payload,'price':str(row['c']),'price_kind':'last','as_of':int(row['E']),'received_at':received};updated.append(iid)
            elif kind=='depthUpdate' and iid:
                key=venue+':execution_book:'+iid;old=s.get(DataCapability,key)
                # This stream is the venue's complete top-20 snapshot, not incremental full depth.
                if old and (old.payload.get('sequence',-1)>=int(row['u']) or received-old.received_at<1000):continue
                bids,asks=row.get('b'),row.get('a')
                if not bids or not asks or D(bids[0][0])>=D(asks[0][0]):continue
                observed=int(row.get('T') or row['E'])
                s.merge(DataCapability(key=key,provider=venue,capability='execution_book',subject=iid,status='fresh',received_at=received,observed_at=observed,expires_at=received+10000,payload={'bids':bids,'asks':asks,'received_at':received,'observed_at':observed,'scope':'execution_market','instrument_id':iid,'quote_kind':'ask','sequence':int(row['u']),'transport':'ws_atomic_top20_snapshot','depth_scope':'visible_top20_only'}))
            elif kind=='kline' and iid:
                candle_packets.append((iid,row))
            elif kind in ('forceOrder','aggTrade'):
                fact=force_event(venue,row) if kind=='forceOrder' else trade_event(venue,row)
                if not fact or fact['instrument_id'] not in instruments.values():continue
                fact['received_at']=received
                s.execute(insert(RawEvent).values(id=fact['id'],source=venue,endpoint='ws:'+fact['kind'],received_at=received,payload=fact).on_conflict_do_nothing(index_elements=['id']))
        if updated:emit(s,'market_update',{'source':venue,'instruments':updated,'transport':'websocket'})
    if candle_packets:
        from engine.adapters.base import Batch
        from engine.repository import save_batch
        batch=Batch(venue,candle_only=True)
        for iid,row in candle_packets:
            k=row['k'];ref=hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()
            batch.raw.append({'id':ref,'source':venue,'endpoint':'ws:kline','received_at':received,'payload':row})
            batch.candles.append({'instrument_id':iid,'resolution':'60','open_time':int(k['t']),'close_time':int(k['T'])+1,'is_closed':bool(k['x']),'open':str(k['o']),'high':str(k['h']),'low':str(k['l']),'close':str(k['c']),'base_volume':str(k['v']),'quote_volume':str(k['q']),'received_at':received,'raw_ref':ref})
        save_batch(batch)

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
        from engine.pd2.universe_policy import membership
        aliases,admitted=membership(s)
        if venue in ('aster','hyperliquid'):
            active={aliases.get(a,a) for a in s.scalars(select(Campaign.asset_id).join(Episode).where(Episode.phase.notin_(['invalidated','ended'])))}
            rows=[i for i in s.scalars(select(Instrument).where(Instrument.venue==venue)) if aliases.get(i.asset_id,i.asset_id) in active]
        return list({i.id:i for i in rows if aliases.get(i.asset_id,i.asset_id) in admitted}.values())

async def stream_venue(venue,stop_after=None,shard=0,shards=1):
    from websockets.asyncio.client import connect
    base={'binance':'wss://fstream.binance.com','aster':'wss://fstream.asterdex.com','binance_spot':'wss://data-stream.binance.vision:443'}[venue]
    backoff=1;started=now_ms()
    while stop_after is None or now_ms()-started<stop_after*1000:
        selected=sorted(await asyncio.to_thread(subscriptions,venue),key=lambda i:i.id)[shard*128:(shard+1)*128] if shards>1 else await asyncio.to_thread(subscriptions,venue)
        globals_=(['!markPrice@arr@1s','!forceOrder@arr'] if venue!='binance_spot' else ['!miniTicker@arr']) if shard==0 else []
        streams=globals_+[i.native_symbol.lower()+suffix for i in selected for suffix in (['@aggTrade','@kline_1h','@depth20@100ms'] if venue=='binance' else ['@aggTrade','@kline_1h'] if venue=='binance_spot' else ['@aggTrade'])]
        if not streams:
            await asyncio.sleep(5);continue
        try:
            async with connect(base+'/stream',ssl=ssl.create_default_context(),open_timeout=15,ping_interval=20,ping_timeout=20,max_size=4*1024*1024) as ws:
                # Subscribe in messages: avoids URL length limits; <=386 streams per connection.
                for n in range(0,len(streams),100):
                    await ws.send(json.dumps({'method':'SUBSCRIBE','params':streams[n:n+100],'id':n+1}))
                    await asyncio.sleep(.25)
                connected=now_ms();await asyncio.to_thread(stream_scope,venue+':'+str(shard),True,[i.id for i in selected],connected)
                backoff=1;last_mark=0;last_health=0;checked=connected;last_kline={}
                while True:
                    if now_ms()-checked>=60000:
                        latest=sorted(await asyncio.to_thread(subscriptions,venue),key=lambda i:i.id);checked=now_ms()
                        if shards>1:latest=latest[shard*128:(shard+1)*128]
                        if {i.id for i in latest}!={i.id for i in selected}:break
                    if stop_after is not None and now_ms()-started>=stop_after*1000:return
                    try:raw=await asyncio.wait_for(ws.recv(),timeout=min(20,max(.1,stop_after-(now_ms()-started)/1000)) if stop_after is not None else 20)
                    except asyncio.TimeoutError:continue
                    packet=json.loads(raw);at=now_ms()
                    if 'code' in packet:raise ValueError('subscription_rejected')
                    if 'result' in packet:continue
                    k=(packet.get('data') or {}).get('k') if isinstance(packet.get('data'),dict) else None
                    if k and not k.get('x'):
                        symbol=k.get('s')
                        if at-last_kline.get(symbol,0)<30000:continue
                        last_kline[symbol]=at
                    # Whole-market marks are persisted at most once per two seconds.
                    if 'markPrice' in packet.get('stream',''):
                        if at-last_mark<2000:continue
                        last_mark=at
                    await asyncio.to_thread(save_packet,venue,packet,at)
                    if at-last_health>=10000:
                        await asyncio.to_thread(heartbeat,'stream_'+venue+(':'+str(shard) if shard else ''),'connected',{'trade_markets':len(selected),'shards':shards,'liquidations':'1000ms_sample' if venue!='binance_spot' else 'not_applicable'});last_health=at
        except Exception as e:
            await asyncio.to_thread(heartbeat,'stream_'+venue,'reconnecting',{'reason':type(e).__name__})
            await asyncio.sleep(min(backoff,max(0,stop_after-(now_ms()-started)/1000)) if stop_after is not None else backoff);backoff=min(backoff*2,30)
        finally:await asyncio.to_thread(stream_scope,venue+':'+str(shard),False,[],now_ms())

async def venue_stream_group(venue):
    tasks=[];count=0
    try:
        while True:
            selected=await asyncio.to_thread(subscriptions,venue)
            wanted=max(1,(len(selected)+127)//128)
            if wanted!=count or any(t.done() for t in tasks):
                for task in tasks:task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
                count=wanted
                tasks=[asyncio.create_task(stream_venue(venue,shard=n,shards=count)) for n in range(count)]
            await asyncio.sleep(60)
    finally:
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)

async def run_streams():
    await asyncio.gather(*(venue_stream_group(v) for v in ('binance','aster','binance_spot')),stream_hyperliquid())


def save_hl_context(data,received):
    from engine.adapters.base import Batch,decimal_text
    from engine.repository import save_batch
    from engine.pd2.universe_policy import auxiliary_symbols
    coin=data.get('coin');ctx=data.get('ctx') or {}
    if coin not in auxiliary_symbols('hyperliquid') or not all(k in ctx for k in ('openInterest','markPx')):return
    iid='hyperliquid:perp:'+coin;qty=D(str(ctx['openInterest']));mark=D(str(ctx['markPx']))
    if not qty.is_finite() or qty<0 or not mark.is_finite() or mark<=0:return
    ref=hashlib.sha256(json.dumps(data,sort_keys=True).encode()+str(received).encode()).hexdigest()
    batch=Batch('hyperliquid')
    batch.raw=[{'id':ref,'source':'hyperliquid','endpoint':'ws:activeAssetCtx','received_at':received,'payload':data}]
    batch.oi=[dict(instrument_id=iid,event_time=received,base_quantity=str(qty),notional_quote=str(qty*mark),quote_asset='USDC',mark_price=str(mark),received_at=received,raw_ref=ref)]
    batch.states=[dict(instrument_id=iid,as_of=received,received_at=received,price=str(mark),price_kind='mark',oi_base=str(qty),oi_notional_quote=str(qty*mark),oi_as_of=received,oi_quote_asset='USDC',oi_status='fresh',predicted_funding_rate=ctx.get('funding'),predicted_funding_interval_seconds=3600,source_time_kind='received_at',raw_ref=ref)]
    save_batch(batch)


async def stream_hyperliquid(stop_after=None):
    from websockets.asyncio.client import connect
    started=now_ms();backoff=1
    while stop_after is None or now_ms()-started<stop_after*1000:
        try:
            async with connect('wss://api.hyperliquid.xyz/ws',ssl=ssl.create_default_context(),open_timeout=15,ping_interval=20,ping_timeout=20) as ws:
                subscribed=set();checked=0;saved={};backoff=1
                while stop_after is None or now_ms()-started<stop_after*1000:
                    now=now_ms()
                    if now-checked>=60000:
                        selected=await asyncio.to_thread(subscriptions,'hyperliquid')
                        desired={i.native_symbol for i in selected}
                        for method,coins in (('unsubscribe',subscribed-desired),('subscribe',desired-subscribed)):
                            for coin in sorted(coins):
                                await ws.send(json.dumps({'method':method,'subscription':{'type':'activeAssetCtx','coin':coin}}))
                        subscribed=desired;checked=now
                        await asyncio.to_thread(heartbeat,'stream_hyperliquid','connected',{'candidate_subscriptions':len(subscribed),'namespace':'default','persist_interval_seconds':15})
                    try:packet=json.loads(await asyncio.wait_for(ws.recv(),timeout=10))
                    except asyncio.TimeoutError:continue
                    if packet.get('channel')!='activeAssetCtx':continue
                    data=packet.get('data') or {};coin=data.get('coin');received=now_ms()
                    if coin in subscribed and received-saved.get(coin,0)>=15000:
                        await asyncio.to_thread(save_hl_context,data,received);saved[coin]=received
        except asyncio.CancelledError:raise
        except Exception as error:
            try:await asyncio.to_thread(heartbeat,'stream_hyperliquid','reconnecting',{'reason':type(error).__name__})
            except Exception:pass
            await asyncio.sleep(backoff);backoff=min(30,backoff*2)

def tape_context(instrument,now):
    with Session() as s:
        inst=s.get(Instrument,instrument)
        if not inst:return {'status':'unknown_instrument'}
        raw=list(s.scalars(select(RawEvent).where(RawEvent.source==inst.venue,RawEvent.endpoint.in_(['ws:trade','ws:liquidation']),RawEvent.received_at>now-86400000,RawEvent.received_at<=now,RawEvent.payload['instrument_id'].as_string()==instrument)))
        facts=[r.payload for r in raw if r.payload.get('instrument_id')==instrument and r.payload.get('event_time',0)<=now]
        coverage=list(s.scalars(select(UserPreference).where((UserPreference.key=='ws_coverage:'+inst.venue)|UserPreference.key.like('ws_coverage:'+inst.venue+':%'))))
        segments=[segment for item in coverage for segment in item.payload.get('segments',[])]
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
