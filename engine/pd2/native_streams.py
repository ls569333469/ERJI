"""Read-only native WebSocket facts; liquidation feeds are explicitly sampled."""
import asyncio,json,hashlib,ssl,logging,time
from collections import deque
from decimal import Decimal as D
from sqlalchemy import select,text
from sqlalchemy.dialects.postgresql import insert
from engine.db import Session
from engine.models import Instrument,MarketState,RawEvent,UserPreference,Campaign,Episode,DataCapability
from engine.adapters.base import now_ms
from engine.repository import emit,heartbeat
from engine.p38.lifecycle import thread
from engine.p39.timing import BOOK_MS

_packet_maps={}
KLINE_RESOLUTIONS={'5m':'5','1h':'60','4h':'240','1d':'1D','1w':'1W'}

class LatestQuotes:
    """One newest snapshot per native symbol/kind; facts use a different queue."""
    def __init__(self,capacity=4096,symbols=None):
        self.capacity=capacity;self.pending={};self.coalesced=0;self.rejected=0
        self.symbols=None if symbols is None else set(symbols)
        self.filtered=0;self.accepted=0;self.committed=0;self.expired=0;self.skipped_write=0;self.writes=deque(maxlen=120)
    def restrict(self,symbols):
        self.symbols=set(symbols)
        for key in list(self.pending):
            if key[0] not in self.symbols:del self.pending[key];self.filtered+=1
    def offer(self,packet,received):
        data=packet.get('data',packet);rows=data if isinstance(data,list) else [data]
        for row in rows:
            if not isinstance(row,dict) or row.get('e') not in ('markPriceUpdate','24hrMiniTicker'):continue
            if self.symbols is not None and row.get('s') not in self.symbols:self.filtered+=1;continue
            at=int(row.get('E') or 0)
            if not 0<=received-at<=15000:self.expired+=1;continue
            key=(row.get('s'),row['e']);old=self.pending.get(key)
            if old and at<=int(old['E']):continue
            if old:self.coalesced+=1
            if key not in self.pending and len(self.pending)>=self.capacity:self.rejected+=1;continue
            self.pending[key]=dict(row,_received_at=received)
            self.accepted+=1
    def drain(self,limit=64):
        keys=list(self.pending)[:limit];return [self.pending.pop(k) for k in keys]
    def restore(self,rows):
        for row in rows:
            if self.symbols is not None and row['s'] not in self.symbols:continue
            key=(row['s'],row['e']);old=self.pending.get(key)
            if old is None and len(self.pending)>=self.capacity:self.rejected+=1;continue
            if old is None or int(old['E'])<int(row['E']):self.pending[key]=row
    def metrics(self):
        times=sorted(self.writes)
        return {'pending':len(self.pending),'admitted_symbols':len(self.symbols) if self.symbols is not None else None,'filtered':self.filtered,'accepted':self.accepted,'committed':self.committed,'expired_at_receive':self.expired,'skipped_write':self.skipped_write,'coalesced':self.coalesced,'overflow':self.rejected,'write_p95_ms':times[min(len(times)-1,int(len(times)*.95))] if times else None,'oldest_pending_age_ms':max((now_ms()-int(r['E']) for r in self.pending.values()),default=0)}

async def write_quotes(venue,buffer):
    limit=128
    while True:
        await asyncio.sleep(.05 if buffer.pending else .5)
        rows=buffer.drain(limit)
        if rows:
            started=time.perf_counter()
            from engine.db_retry import retry_write
            try:updated=await retry_write(lambda:thread(save_packet,venue,{'data':rows},max(r['_received_at'] for r in rows)))
            except BaseException:
                buffer.restore(rows);raise
            elapsed=max(.001,time.perf_counter()-started);buffer.writes.append(round(elapsed*1000,2));buffer.committed+=updated or 0;buffer.skipped_write+=len(rows)-(updated or 0)
            # Aim for one second per transaction, bounded to 64..512 rows.
            limit=max(64,min(512,int(len(rows)/elapsed)))


class LatestDepth:
    """Bounded coalescing for full top20 snapshots only, never trade deltas."""
    def __init__(self, symbols):
        self.symbols=set(symbols);self.pending={}

    def offer(self, packet, received):
        row=packet.get('data',packet)
        if not isinstance(row,dict) or row.get('e')!='depthUpdate' or row.get('s') not in self.symbols:return
        old=self.pending.get(row['s'])
        if old and int(row['u'])<=int(old[0]['u']):return
        self.pending[row['s']]=(row,received)

    def drain(self):
        rows=self.pending;self.pending={}
        return list(rows.values())


async def write_depth(venue, buffer):
    while True:
        await asyncio.sleep(.5)
        rows=buffer.drain()
        # Preserve original reception times. Batching cannot rejuvenate data.
        if rows:
            from engine.db_retry import retry_write
            try:await retry_write(lambda:thread(save_depth_batch,venue,rows))
            except BaseException:
                for row,at in rows:buffer.offer(row,at)
                raise


def save_depth_batch(venue, rows):
    # Group equal reception times; each row keeps its own original timestamp.
    # A single transaction per drained batch avoids one checkout per packet.
    save_packet(venue,{'data':[dict(row,_received_at=at) for row,at in rows]},now_ms())

def combine_packets(items):
    entries=[]
    for packet,at in items:
        rows=packet.get('data',packet);rows=rows if isinstance(rows,list) else [rows]
        entries.extend(dict(r,_received_at=r.get('_received_at',at)) for r in rows)
    return {'data':entries},max(at for _,at in items)
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
        quote_ids={instruments[r['s']] for r in entries if r.get('e') in ('markPriceUpdate','24hrMiniTicker') and r.get('s') in instruments}
        quote_states={st.instrument_id:st for st in s.scalars(select(MarketState).where(
            MarketState.instrument_id.in_(quote_ids)).order_by(MarketState.instrument_id).with_for_update())} if quote_ids else {}
        quote_ref=None
        if quote_ids:
            quote_ref=hashlib.sha256((venue+str(received)+json.dumps(data,sort_keys=True)).encode()).hexdigest()
            s.execute(insert(RawEvent).values(id=quote_ref,source=venue,endpoint='ws:quote_batch',received_at=received,payload={'data':data}).on_conflict_do_nothing(index_elements=['id']))
        for row in sorted(entries,key=lambda r:instruments.get(r.get('s'),'')):
            kind=row.get('e');native=row.get('s');iid=instruments.get(native)
            quote_received=row.get('_received_at',received)
            if kind in ('markPriceUpdate','24hrMiniTicker') and (not 0<=quote_received-int(row['E'])<=15000 or not 0<=now_ms()-int(row['E'])<=15000):continue
            if kind=='markPriceUpdate' and iid:
                state=quote_states.get(iid)
                if not state or int(row['E'])<state.as_of:continue
                state.as_of=int(row['E']);state.payload={**state.payload,'price':str(row['p']),'price_kind':'mark','as_of':int(row['E']),'received_at':quote_received,'raw_ref':quote_ref,'index_price':row.get('i'),'predicted_funding_rate':row.get('r'),'next_funding_time':row.get('T')};updated.append(iid)
            elif kind=='24hrMiniTicker' and iid:
                state=quote_states.get(iid)
                if state and int(row['E'])>=state.as_of:
                    state.as_of=int(row['E']);state.payload={**state.payload,'price':str(row['c']),'price_kind':'last','as_of':int(row['E']),'received_at':quote_received,'raw_ref':quote_ref};updated.append(iid)
            elif kind=='depthUpdate' and iid:
                row=dict(row);depth_received=row.pop('_received_at',received)
                key=venue+':execution_book:'+iid
                s.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':'capability:'+key})
                old=s.get(DataCapability,key,populate_existing=True)
                # This stream is the venue's complete top-20 snapshot, not incremental full depth.
                if old and (old.payload.get('sequence',-1)>=int(row['u']) or depth_received-old.received_at<1000):continue
                bids,asks=row.get('b'),row.get('a')
                if not bids or not asks or D(bids[0][0])>=D(asks[0][0]):continue
                observed=int(row.get('T') or row['E'])
                if not 0<=depth_received-observed<=BOOK_MS or not 0<=now_ms()-observed<=BOOK_MS:continue
                if old and old.observed_at and observed<old.observed_at:continue
                ref=hashlib.sha256((venue+json.dumps(row,sort_keys=True)).encode()).hexdigest()
                s.execute(insert(RawEvent).values(id=ref,source=venue,endpoint='ws:depthUpdate',received_at=depth_received,payload=row).on_conflict_do_nothing(index_elements=['id']))
                s.merge(DataCapability(key=key,provider=venue,capability='execution_book',subject=iid,status='fresh',received_at=depth_received,observed_at=observed,expires_at=observed+BOOK_MS,
                    raw_ref=ref,last_success_at=depth_received,last_success_raw_ref=ref,last_attempt_at=depth_received,last_attempt_raw_ref=ref,
                    payload={'bids':bids,'asks':asks,'received_at':depth_received,'observed_at':observed,'raw_ref':ref,'scope':'execution_market','instrument_id':iid,'quote_kind':'ask','sequence':int(row['u']),'transport':'ws_atomic_top20_snapshot','depth_scope':'visible_top20_only'}))
            elif kind=='kline' and iid:
                candle_packets.append((iid,row))
            elif kind in ('forceOrder','aggTrade'):
                fact=force_event(venue,row) if kind=='forceOrder' else trade_event(venue,row)
                if not fact or fact['instrument_id'] not in instruments.values():continue
                fact['received_at']=quote_received
                s.execute(insert(RawEvent).values(id=fact['id'],source=venue,endpoint='ws:'+fact['kind'],received_at=quote_received,payload=fact).on_conflict_do_nothing(index_elements=['id']))
        if updated:emit(s,'market_update',{'source':venue,'instruments':updated,'transport':'websocket'})
    if candle_packets:
        from engine.adapters.base import Batch
        from engine.repository import save_batch
        batch=Batch(venue,candle_only=True)
        for iid,row in candle_packets:
            received_at=row.get('_received_at',received)
            k=row['k'];ref=hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()
            resolution=KLINE_RESOLUTIONS.get(k.get('i'))
            if resolution is None:continue
            batch.raw.append({'id':ref,'source':venue,'endpoint':'ws:kline','received_at':received_at,'payload':row})
            batch.candles.append({'instrument_id':iid,'resolution':resolution,'open_time':int(k['t']),'close_time':int(k['T'])+1,'is_closed':bool(k['x']),'open':str(k['o']),'high':str(k['h']),'low':str(k['l']),'close':str(k['c']),'base_volume':str(k['v']),'quote_volume':str(k['q']),'received_at':received_at,'raw_ref':ref})
        save_batch(batch)
    return len(updated)

def stream_scope(venue,connected,instruments,at):
    with Session.begin() as s:
        key='ws_coverage:'+venue;r=s.get(UserPreference,key);history=(r.payload if r else {}).get('segments',[])
        if history and history[-1].get('end') is None:history[-1]['end']=at
        if connected:history.append({'start':at,'end':None,'instruments':instruments})
        s.merge(UserPreference(key=key,updated_at=at,payload={'connected':connected,'segments':history[-200:]}))

def subscriptions(venue):
    if venue not in ('binance','binance_spot'):return []
    with Session() as s:
        rows=list(s.scalars(select(Instrument).join(Campaign,Campaign.reference_instrument_id==Instrument.id).join(Episode).where(Instrument.venue==venue,Episode.phase.notin_(['invalidated','ended']))))
        if venue=='binance_spot':
            active_assets=set(s.scalars(select(Campaign.asset_id).join(Episode).where(Episode.phase.notin_(['invalidated','ended']))))
            rows=list(s.scalars(select(Instrument).where(Instrument.venue==venue,Instrument.asset_id.in_(active_assets),Instrument.quote_asset=='USDT')))
        from engine.pd2.universe_policy import membership
        aliases,admitted=membership(s)
        return list({i.id:i for i in rows if aliases.get(i.asset_id,i.asset_id) in admitted}.values())


def touch_scope(key,at):
    with Session.begin() as session:
        row=session.get(UserPreference,'ws_coverage:'+key)
        if row and row.payload.get('connected'):row.updated_at=at


def proven_segments(records,now):
    """An open segment is only proven through its persisted receiver heartbeat."""
    return [{**segment,'end':min(segment.get('end') or record.updated_at,now)}
            for record in records for segment in record.payload.get('segments',[])
            if segment['start']<=min(segment.get('end') or record.updated_at,now)]

def stream_route(venue,selected,shard=0,channel=None):
    """Binance market facts and order books use different streaming routes.

    Global marks get their own receiver so a busy trade tape cannot delay quotes.
    Subscriptions and persistence frequency are unchanged.
    """
    if venue=='binance':
        if channel=='global':return '/market/stream',['!markPrice@arr@1s','!forceOrder@arr']
        suffixes=['@depth20@100ms'] if channel=='depth' else ['@aggTrade','@kline_1h','@kline_4h','@kline_1d','@kline_1w']
        return ('/public/stream' if channel=='depth' else '/market/stream'),[i.native_symbol.lower()+suffix for i in selected for suffix in suffixes]
    if venue=='binance_spot' and channel=='global':return '/stream',['!miniTicker@arr']
    globals_=(['!markPrice@arr@1s','!forceOrder@arr'] if venue!='binance_spot' else ['!miniTicker@arr']) if shard==0 and channel!='market' else []
    suffixes=['@aggTrade','@kline_1h','@kline_4h','@kline_1d','@kline_1w'] if venue=='binance_spot' else ['@aggTrade']
    return '/stream',globals_+[i.native_symbol.lower()+suffix for i in selected for suffix in suffixes]


async def stream_venue(venue,stop_after=None,shard=0,shards=1,channel=None):
    if venue not in ('binance','binance_spot'):return
    from websockets.asyncio.client import connect
    from engine.p40.streams import ReliableClientConnection,ReceiveTimings,StreamLifecycle,retry_delay
    base={'binance':'wss://fstream.binance.com','binance_spot':'wss://data-stream.binance.vision:443'}[venue]
    scope_key=venue+(':'+channel if channel else '')+':'+str(shard)
    health_role='stream_'+venue+('' if channel=='global' else ':'+channel+':'+str(shard) if channel else ':'+str(shard) if shard else '')
    backoff=1;started=now_ms()
    while stop_after is None or now_ms()-started<stop_after*1000:
        selected=[] if channel=='global' else sorted(await asyncio.to_thread(subscriptions,venue),key=lambda i:i.id)[shard*128:(shard+1)*128]
        route,streams=stream_route(venue,selected,shard,channel)
        if not streams:
            await asyncio.sleep(5);continue
        writer=None
        fact_writer=None
        def admitted_symbols():
            with Session() as s:return set(packet_instruments(s,venue,now_ms()))
        quote_writer=None;maintenance=None;quotes=LatestQuotes(symbols=await thread(admitted_symbols))
        reception={'at':None,'resubscribe':False};timings=ReceiveTimings()
        from engine.p38.loop_health import LoopHealth
        health=LoopHealth()
        lifecycle=StreamLifecycle(health_role,venue,channel or 'combined',streams);retry=0
        try:
            async with connect(base+route,ssl=ssl.create_default_context(),open_timeout=15,ping_interval=20,ping_timeout=20,max_size=4*1024*1024,create_connection=ReliableClientConnection) as ws:
                lifecycle.attach(ws)
                # Subscribe in batches; at most 640 per 128-market connection.
                for n in range(0,len(streams),100):
                    await ws.send(json.dumps({'method':'SUBSCRIBE','params':streams[n:n+100],'id':n+1}))
                    await asyncio.sleep(.25)
                connected=now_ms();await asyncio.to_thread(stream_scope,scope_key,True,[i.id for i in selected] if channel!='depth' else [],connected)
                lifecycle.connected();last_mark=0;last_health=0;checked=connected;last_kline={}
                from engine.repository import runtime_config
                from engine.pd2.p35_schedule import windows as required_windows
                window_sizes=required_windows(await asyncio.to_thread(runtime_config))
                buffer=LatestDepth(i.native_symbol for i in selected)
                if channel=='depth':writer=asyncio.create_task(write_depth(venue,buffer))
                else:
                    from engine.pd2.write_queue import Writer,failure_directory
                    async def persist(item):await asyncio.to_thread(save_packet,venue,*item)
                    fact_writer=Writer(persist,256,combine_packets,failure_path=failure_directory('native:'+scope_key))
                    fact_writer.task=asyncio.create_task(fact_writer.run())
                    quote_writer=asyncio.create_task(write_quotes(venue,quotes))
                async def refresh_health():
                    nonlocal checked
                    tick=now_ms()
                    if channel!='global' and tick-checked>=60000:
                        latest=sorted(await thread(subscriptions,venue),key=lambda i:i.id);checked=tick
                        latest=latest[shard*128:(shard+1)*128]
                        reception['resubscribe']={i.id for i in latest}!={i.id for i in selected}
                    quotes.restrict(await thread(admitted_symbols))
                    at=reception['at']
                    if at is None:return
                    await thread(touch_scope,scope_key,at)
                    await thread(heartbeat,health_role,'connected',{'trade_markets':len(selected) if channel!='depth' else 0,'shards':shards,'channel':channel or 'combined','quote_pending':len(quotes.pending),'quote_coalesced':quotes.coalesced,'quote_overflow':quotes.rejected,'quote_writer':quotes.metrics(),'last_receive_at':at,'write_queue':fact_writer.metrics() if fact_writer else None,'receive_timings':timings.metrics(),'loop_lag':health.metrics(),'lifecycle':lifecycle.metrics(),'liquidations':'1000ms_sample' if venue!='binance_spot' else 'not_applicable'})
                from engine.pd2.stream_maintenance import maintain
                maintenance=asyncio.create_task(maintain(refresh_health))
                while True:
                    if writer and writer.done():await writer
                    if quote_writer and quote_writer.done():await quote_writer
                    if fact_writer and fact_writer.task.done():await fact_writer.task;raise RuntimeError('fact_writer_exited')
                    if maintenance.done():await maintenance;raise RuntimeError('stream_maintenance_exited')
                    if reception['resubscribe']:break
                    if stop_after is not None and now_ms()-started>=stop_after*1000:return
                    try:raw=await asyncio.wait_for(ws.recv(),timeout=min(20,max(.1,stop_after-(now_ms()-started)/1000)) if stop_after is not None else 20)
                    except asyncio.TimeoutError:continue
                    packet=json.loads(raw);at=now_ms();reception['at']=at;timings.received(packet,at)
                    if timings.stable(at):backoff=1
                    from engine.p37.detector import ingest
                    ingest_started=time.perf_counter();ingest(venue,packet,at,lifecycle.connection_id);timings.record('ingest_ms',ingest_started)
                    if 'code' in packet:raise ValueError('subscription_rejected')
                    if 'result' in packet:continue
                    k=(packet.get('data') or {}).get('k') if isinstance(packet.get('data'),dict) else None
                    if k:
                        from engine.pd2.candle_window import windows
                        windows.update((venue,k.get('s'),k.get('i')),[{'open_time':int(k['t']),'close_time':int(k['T'])+1,'is_closed':bool(k['x']),'received_at':at,'open':k['o'],'high':k['h'],'low':k['l'],'close':k['c']}],window_sizes.get(KLINE_RESOLUTIONS.get(k.get('i')),300)+1)
                    if k and not k.get('x'):
                        symbol=(k.get('s'),k.get('i'))
                        if at-last_kline.get(symbol,0)<30000:continue
                        last_kline[symbol]=at
                    if channel=='depth':buffer.offer(packet,at)
                    else:
                        data=packet.get('data',packet);entries=data if isinstance(data,list) else [data]
                        quotes.offer(packet,at)
                        facts=[r for r in entries if isinstance(r,dict) and r.get('e') not in ('markPriceUpdate','24hrMiniTicker')]
                        if facts:
                            from engine.p40.streams import measured_writer_put
                            await measured_writer_put(ws,fact_writer,({'data':facts},at),timings)
        except Exception as e:
            lifecycle.pipeline_snapshot(last_receive_at=reception['at'],receive_timings=timings.metrics(),write_queue=fact_writer.metrics() if fact_writer else None,loop_lag=health.metrics())
            retry=retry_delay(backoff);lifecycle.end('failure',e,retry);backoff=min(backoff*2,30)
            # Stop connected-heartbeat writes before publishing the close record.
            if maintenance:
                maintenance.cancel();await asyncio.gather(maintenance,return_exceptions=True);maintenance=None
            await thread(heartbeat,health_role,'reconnecting',{'reason':type(e).__name__,'last_receive_at':reception['at'],'write_queue':fact_writer.metrics() if fact_writer else None,'receive_timings':timings.metrics(),'loop_lag':health.metrics(),'lifecycle':lifecycle.metrics()})
        finally:
            lifecycle.end('intentional_cancel' if asyncio.current_task().cancelling() else 'subscription_change' if reception['resubscribe'] else 'stop_after')
            await health.close()
            if maintenance:
                maintenance.cancel();await asyncio.gather(maintenance,return_exceptions=True)
            if quote_writer:
                quote_writer.cancel();await asyncio.gather(quote_writer,return_exceptions=True)
            if fact_writer:
                try:await fact_writer.close()
                finally:await thread(stream_scope,scope_key,False,[],now_ms())
            if writer:
                writer.cancel();await asyncio.gather(writer,return_exceptions=True)
            if not fact_writer:await thread(stream_scope,scope_key,False,[],now_ms())
        # Drain retained facts and return resources before backing off.
        if retry:await asyncio.sleep(min(retry,max(0,stop_after-(now_ms()-started)/1000)) if stop_after is not None else retry)

async def venue_stream_group(venue):
    if venue not in ('binance','binance_spot'):return
    tasks={}
    try:
        while True:
            from engine.repository import runtime_config
            enabled=(await asyncio.to_thread(runtime_config)).collector_enabled
            selected=await asyncio.to_thread(subscriptions,venue)
            count=max(1,(len(selected)+127)//128) if enabled else 0
            wanted=[('global',0)] if enabled else []
            channels=('market','depth') if venue=='binance' else ('market',)
            wanted += [(channel,n) for channel in channels for n in range(count)]
            from engine.p40.receivers import reconcile
            # Changing the number of strategy markets must not tear down the
            # global price feed and erase unrelated price-window continuity.
            await reconcile(tasks,wanted,lambda key:stream_venue(venue,channel=key[0],shard=key[1],shards=count))
            await asyncio.sleep(60)
    finally:
        for task in tasks.values():task.cancel()
        await asyncio.gather(*tasks.values(),return_exceptions=True)

async def run_streams(additional=()):
    # Composition lives in the service entrypoint. Collection does not import
    # the observation/strategy worker back into the transport module.
    tasks=[asyncio.create_task(supervise_stream(v,lambda venue=v:venue_stream_group(venue)))
           for v in ('binance','binance_spot')]
    tasks.extend(asyncio.create_task(supervise_stream(name,fn)) for name,fn in additional)
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)


async def supervise_stream(name,run,stable=None):
    """A database/receiver failure must not terminate the other market feeds."""
    backoff=5;restarts=0;last_error=None;last_error_at=None
    while True:
        started=now_ms();task=None;retry=None
        try:
            task=asyncio.create_task(run())
            while not task.done():
                try:
                    await asyncio.to_thread(heartbeat,'stream_supervisor_'+name,'running',
                        {'started_at':started,'restarts':restarts,'last_error':last_error,'last_error_at':last_error_at,
                         'scope':'supervised coroutine lifecycle; receiver health is separate'})
                except Exception:pass
                await asyncio.wait((task,),timeout=60)
            await task
        except asyncio.CancelledError:raise
        except Exception as error:
            restarts+=1;last_error=type(error).__name__;last_error_at=now_ms()
            from engine.p40.streams import retry_delay
            if (stable() if stable is not None else now_ms()-started>60000):backoff=5
            retry=retry_delay(backoff)
            logging.getLogger('erji.streams').warning('%s receiver restarting: %s',name,last_error)
            try:await asyncio.to_thread(heartbeat,'stream_supervisor_'+name,'reconnecting',{'reason':last_error,'restarts':restarts,'last_error_at':last_error_at,'retry_seconds':retry,'next_retry_at':now_ms()+int(retry*1000)})
            except Exception:pass  # A failed health write cannot kill supervision.
        finally:
            if task:
                task.cancel();await asyncio.gather(task,return_exceptions=True)
        if retry is None:
            from engine.p40.streams import retry_delay
            if (stable() if stable is not None else now_ms()-started>60000):backoff=5
            retry=retry_delay(backoff)
        await asyncio.sleep(retry)
        backoff=min(60,backoff*2)





def tape_context(instrument,now):
    with Session() as s:
        inst=s.get(Instrument,instrument)
        if not inst:return {'status':'unknown_instrument'}
        raw=list(s.scalars(select(RawEvent).where(RawEvent.source==inst.venue,RawEvent.endpoint.in_(['ws:trade','ws:liquidation']),RawEvent.received_at>now-86400000,RawEvent.received_at<=now,RawEvent.payload['instrument_id'].as_string()==instrument)))
        facts=[r.payload for r in raw if r.payload.get('instrument_id')==instrument and r.payload.get('event_time',0)<=now]
        coverage=list(s.scalars(select(UserPreference).where((UserPreference.key=='ws_coverage:'+inst.venue)|UserPreference.key.like('ws_coverage:'+inst.venue+':%'))))
        segments=proven_segments(coverage,now)
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
