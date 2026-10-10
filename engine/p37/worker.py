"""Runs beside existing streams. Detection reads memory; writes are coalesced."""
import asyncio,time
from sqlalchemy import select
from engine.db import Session
from engine.models import Observation,Candle,UserPreference,Task
from engine.p37.detector import detector
from engine.p37.store import catalog,persist,refresh_tracking
from engine.repository import heartbeat
from engine.p39.timing import ORDINARY_MS,HOT_MS,HEARTBEAT_MS,due_at,analysis_wakeup

discovery_progress={'state':'warming','details':{'scan_finished_at':None}}
discovery_visits={}


def discovery_health_snapshot(source, clock=None):
    """One coherent source/clock snapshot; window work cannot block ingestion."""
    from engine.p37.detector import PriceView
    with source.lock:
        at=clock() if clock else int(time.time()*1000)
        config=dict(source.config)
        meta={k:dict(v) for k,v in source.catalog.items()}
        prices={k:tuple(source.prices.get(k,())) for k in meta}
        oi={k:tuple(source.oi.get(k,())) for k in meta}
        receipts={k:{**v,'rejections':dict(v['rejections']),'recent_gaps':list(v.get('recent_gaps',[]))} for k,v in source.price_receipts.items() if k in meta}
        oi_receipts={'scope':'bounded per-market ingestion/evaluation evidence; no inferred lost signals',
            'examples':[{'instrument_id':iid,'ingestion':list(source.oi_receipts.get(iid,())),
                'evaluation':list(source.oi_evaluations.get(iid,()))} for iid in sorted(meta) if source.oi_receipts.get(iid) or source.oi_evaluations.get(iid)][:30]}
    # Build the continuity index once per market, then evaluate each window
    # once. Eligible coverage reuses those same answers.
    windows={};statuses={};fresh=0
    for iid,rows in prices.items():
        current=bool(rows) and 0<=at-rows[-1][0]<=15000
        fresh+=current
        view=PriceView(rows,{})
        windows[iid]={minutes:view.delta(at,minutes*60000) is not None for minutes in (5,15)}
        from engine.p40.diagnostics import window_status
        statuses[iid]={str(w):'ready' if windows[iid][w] else window_status(rows,at,w*60000,view=view) for w in (5,15)}
    eligible=[k for k,v in meta.items() if (v.get('liquidity') or 0)>=config['liquidity_min']]
    pending=[{'symbol':v['symbol'],'instrument_id':k,
        'reason':'流动性待核实' if v.get('liquidity') is None else '24小时成交额未达基础门槛',
        'field_status':v.get('liquidity_status') if v.get('liquidity') is None else 'below_threshold',
        'liquidity':v.get('liquidity'),'source_at':v.get('liquidity_source_at'),
        'received_at':v.get('liquidity_received_at')} for k,v in meta.items()
        if v.get('liquidity') is None or v['liquidity']<config['liquidity_min']]
    from engine.p40.diagnostics import window_metrics
    coverage,oi_coverage=window_metrics(meta,prices,oi,config,at,windows,statuses)
    from engine.p40.diagnostics import price_receipt_sample
    return {'snapshot_at':at,'meta':meta,'fresh':fresh,'eligible':eligible,'pending':pending,
        'ready':sum(w[5] for w in windows.values()),'ready15':sum(w[15] for w in windows.values()),
        'coverage':coverage,'oi_coverage':oi_coverage,
        'price_receipts':price_receipt_sample(meta,prices,receipts,config,at,statuses=statuses),
        'oi_receipts':oi_receipts}

async def discovery_heartbeat():
    while True:
        await asyncio.to_thread(heartbeat,'anomaly_discovery',discovery_progress['state'],dict(discovery_progress['details']))
        await asyncio.sleep(HEARTBEAT_MS/1000)

def seed_memory():
    from sqlalchemy import values,column,String,true
    from sqlalchemy.orm import aliased
    ids=list(detector.catalog)
    if ids:
        demand=values(column('iid',String),name='candle_seed').data([(iid,) for iid in ids])
        latest=select(Candle).where(Candle.instrument_id==demand.c.iid,Candle.resolution=='5').order_by(Candle.open_time.desc()).limit(11).lateral()
        candle=aliased(Candle,latest)
        with Session() as s:rows=list(s.scalars(select(candle).select_from(demand.join(latest,true())).order_by(latest.c.open_time)))
        for r in rows:detector.bar(r.instrument_id,dict(open_time=r.open_time,open=str(r.open),close=str(r.close),volume=str(r.quote_volume) if r.quote_volume is not None else None,closed=r.is_closed,at=r.received_at))
    seed_oi_memory()


def seed_oi_memory(now=None):
    """Restore only real received native OI endpoints, never the offline gap.

    Price windows still require continuous live ticks after a local restart.
    OI quantity endpoints can reuse persisted points when genuinely fresh.
    """
    from collections import deque
    from sqlalchemy import values,column,String,true
    from engine.models import OiPoint
    now=int(time.time()*1000) if now is None else now
    ids=[iid for iid in detector.catalog if iid.startswith('binance:')]
    if not ids:return
    demand=values(column('iid',String),name='oi_seed').data([(iid,) for iid in ids])
    latest=select(OiPoint.event_time,OiPoint.base_quantity,OiPoint.received_at).where(OiPoint.instrument_id==demand.c.iid,
        OiPoint.event_time>=now-960000,OiPoint.event_time<=now,OiPoint.received_at<=now,
        OiPoint.base_quantity.is_not(None)).order_by(OiPoint.event_time.desc()).limit(160).lateral()
    with Session() as s:points=list(s.execute(select(demand.c.iid,latest).select_from(demand.join(latest,true()))))
    grouped={}
    receipts={}
    for iid,at,quantity,received in points:
        grouped.setdefault(iid,{})[at]=str(quantity);receipts.setdefault(iid,{})[at]=received
    with detector.lock:
        for iid,points in grouped.items():
            points.update(dict(detector.oi.get(iid,())))
            detector.oi[iid]=deque(sorted(points.items())[-160:],maxlen=160)
            detector.oi_known[iid]=deque(sorted(receipts.get(iid,{}).items())[-160:],maxlen=160)
            detector.oi_receipts[iid]=deque([{'source_at':stamp,'known_at':receipts[iid].get(stamp),
                'ingested_at':now,'origin':'persisted_seed','accepted':True} for stamp in sorted(receipts[iid])[-8:]],maxlen=8)

from engine.p37.selection import representative_key

def select_work(now,capacity,work_kind='structure'):
    from engine.p39.pipeline import enabled, select_work as demand_work
    if enabled():return demand_work(now,capacity,work_kind,detector.config)
    return legacy_select_work(now,capacity,work_kind)

def legacy_select_work(now,capacity,work_kind='structure'):
    with Session.begin() as s:
        from engine.models import Instrument
        from engine.pd2.universe_policy import primary_eligible
        rows=[r for r,i in s.execute(select(Observation,Instrument).join(Instrument,Observation.instrument_id==Instrument.id).where(Observation.visible_at.is_not(None),Observation.status.in_(['observing','focused','cooling'])).order_by(Observation.visible_at.desc())) if primary_eligible(i)]
        unique={}
        for r in sorted(rows,key=representative_key):
            unique.setdefault(r.asset_id,r)
        rows=[r for r in unique.values() if r.payload.get(work_kind+'_next_retry_at',0)<=now]
        stamp='structure_checked_at' if work_kind=='structure' else 'analysis_checked_at'
        key='p39_'+work_kind+'_active'
        active=s.get(UserPreference,key,with_for_update=True)
        legacy=s.get(UserPreference,'p37_active') if active is None else None
        previous=active.payload if active else legacy.payload if legacy else {}
        admissions=previous.get('admitted_at',{})
        from engine.p38.jobs import KINDS
        pending={p.get('subject') for p in s.scalars(select(Task.payload).where(
            Task.kind==KINDS[work_kind],Task.state.in_(['queued','leased','failed']))) if not p.get('retired_reason')}
        protected=sorted([r for r in rows if r.payload.get('pinned') or r.payload.get('trade_status')=='active'],key=lambda r:admissions.get(r.instrument_id,0))
        other=[r for r in rows if r not in protected]
        # A trend completion must not evict a still-queued structure. Complete
        # the admitted pair before rotating ordinary slots, then use fair age.
        held=[r for r in other if r.instrument_id in previous.get('instrument_ids',[]) and r.instrument_id in pending and now-admissions.get(r.instrument_id,0)<120000]
        remaining=[r for r in other if r not in held]
        selected=(protected+held+sorted(remaining,key=lambda r:(bool(r.payload.get(stamp)),r.payload.get(stamp,0),admissions.get(r.instrument_id,0))))[:capacity]
        retained={r.instrument_id:admissions.get(r.instrument_id,0) for r in rows}
        for r in selected:
            if r.instrument_id not in pending:retained[r.instrument_id]=now
        value={'instrument_ids':[r.instrument_id for r in selected],'admitted_at':retained}
        if active:active.updated_at=now;active.payload=value
        else:s.add(UserPreference(key=key,updated_at=now,payload=value))
        companion=s.get(UserPreference,'p39_'+('trend' if work_kind=='structure' else 'structure')+'_active')
        union=set(value['instrument_ids'])|set((companion.payload if companion else {}).get('instrument_ids',[]))
        s.merge(UserPreference(key='p37_active',updated_at=now,payload={'instrument_ids':sorted(union),'source':'bounded_structure_trend_union'}))
        changed=set(previous.get('instrument_ids',[]))^set(value['instrument_ids'])
        if changed:
            from engine.repository import emit
            emit(s,'observation_update',{'instruments':sorted(changed)})
        # Submission itself deduplicates by each kind's inputs. The trend clock
        # cannot gate structure submission, including an unchanged-input retry.
        return [(r.id,r.instrument_id) for r in selected],max(0,len(protected)-capacity)

def window_targets(iid,deep,now):
    from engine.p37.analysis import SOURCE_5M_BARS
    targets=[]
    with Session() as s:
        for tf,interval,step,needed in [('5','5m',300000,SOURCE_5M_BARS if deep else 11),('60','1h',3600000,300),('240','4h',14400000,300),('1D','1d',86400000,300)]:
            if not deep and tf!='5':continue
            rows=list(s.scalars(select(Candle).where(Candle.instrument_id==iid,Candle.resolution==tf,Candle.is_closed.is_(True)).order_by(Candle.open_time.desc()).limit(needed)))
            times={r.open_time for r in rows};expected=now//step*step
            listed=detector.catalog.get(iid,{}).get('onboard_time') or 0
            first=max(expected-needed*step,((listed+step-1)//step)*step)
            missing=next((t for t in range(first,expected,step) if t not in times),None)
            if expected-step>=first and expected-step not in times:missing=expected-step
            if missing is not None:targets.append((tf,interval,min(1000,needed+1),missing))
    return targets

def choose_window_slots(ids,priority,slots,turn):
    chosen=[]
    for _ in range(slots):
        deep=turn%4!=0;turn+=1
        available=[iid for iid in ids if iid not in chosen]
        preferred=[iid for iid in available if (iid in priority)==deep]
        if not available:break
        chosen.append((preferred or available)[0])
    return chosen,turn

async def collect_windows():
    from engine.adapters.binance_universe import BinanceUniverseAdapter
    from engine.adapters.expanded_markets import BinanceCashAdapter
    from engine.pd2.continuous_collection import dispatch
    adapters={'binance':BinanceUniverseAdapter(),'binance_spot':BinanceCashAdapter()}
    visited={};attempts={};next_due={};inflight=set();priority=set();selected_at=0;collection_enabled=True;turn=0
    async def choose(slots):
        nonlocal priority,selected_at,collection_enabled,turn
        now=int(time.time()*1000)
        if now-selected_at>=60000:
            from engine.repository import runtime_config
            collection_enabled=(await asyncio.to_thread(runtime_config)).collector_enabled
            from engine.p39.priority import hot_markets
            def active():return hot_markets(now)
            priority=await asyncio.to_thread(active);selected_at=now
        if not collection_enabled:return []
        ids=[iid for iid in detector.catalog if iid not in inflight and now>=next_due.get(iid,0)]
        ids.sort(key=lambda iid:visited.get(iid,0))
        # Reserve one of four admissions, including when slots free one at a time.
        choices,turn=choose_window_slots(ids,priority,slots,turn)
        for iid in choices[:slots]:inflight.add(iid)
        return choices[:slots]
    async def execute(iid):
        retry_ms=60000 if iid in priority else 300000
        try:
            meta=detector.catalog.get(iid)
            if not meta:return
            targets=await asyncio.to_thread(window_targets,iid,iid in priority,int(time.time()*1000))
            targets.sort(key=lambda t:attempts.get((iid,t[0]),0))
            for tf,interval,limit,start in targets[:1]:
                attempts[(iid,tf)]=int(time.time()*1000)
                from engine.p38.candle_gaps import fetch
                batch=await fetch(adapters[meta['venue']],(iid,meta['symbol'],tf,interval,limit,start),'observed' if iid in priority else 'baseline')
                if batch is None:retry_ms=1000 if iid in priority else 60000;return
                if batch.candles:
                    if tf=='5':
                        for r in batch.candles:detector.bar(iid,dict(open_time=r['open_time'],open=r['open'],close=r['close'],volume=r.get('quote_volume'),closed=r['is_closed'],at=r['received_at']))
                retry_ms=15000 if batch.errors else 1000 if iid in priority else 60000
                if batch.errors:await asyncio.to_thread(heartbeat,'observation_windows','partial',{'errors':batch.errors[:3]})
        finally:
            visited[iid]=int(time.time()*1000);next_due[iid]=visited[iid]+retry_ms;inflight.discard(iid)
    async def enabled():return True
    await dispatch(choose,execute,enabled,4)

async def analyze_loop():
    from engine.p38.jobs import submit
    candidate_checked_at=0
    while True:
        from engine.p39.pipeline import enabled, wake_candidates
        if enabled():
            at=int(time.time()*1000)
            if at-candidate_checked_at>=ORDINARY_MS:
                with detector.lock:prices={iid:q[-1] for iid,q in detector.prices.items() if q}
                await asyncio.to_thread(wake_candidates,prices,at);candidate_checked_at=at
        overflow=0;counts={}
        for kind in ('trend','structure'):
            backlog,extra=await asyncio.to_thread(select_work,int(time.time()*1000),detector.config['capacity'],kind)
            counts[kind]=len(backlog);overflow+=extra
            await asyncio.to_thread(submit,[iid for _,iid in backlog],'observation',work_kind=kind)
        await asyncio.to_thread(heartbeat,'observation_analysis','partial' if overflow else 'ok',{'by_kind':counts,'priority_overflow':overflow,'execution_owner':'p39_typed'})
        await analysis_wakeup.wait(30)

async def discover():
    from collections import deque
    timings=deque(maxlen=300)
    oi_probe_at=0;oi_probe_offset=0;oi_probe={}
    loaded=0;tracked=0;reported=0;visited=discovery_visits;consumed_oi={};last_progress={};events=[];count=0
    while True:
        now=int(time.time()*1000)
        if now-loaded>=HEARTBEAT_MS:
            meta,config=await asyncio.to_thread(catalog)
            with detector.lock:
                detector.catalog=meta;detector.config=config
                for mapping in (detector.prices,detector.price_kinds,detector.oi,detector.bars,detector.price_receipts,detector.oi_receipts,detector.oi_evaluations):
                    for iid in list(mapping):
                        if iid not in meta:del mapping[iid]
                for iid,m in meta.items():detector.oi_point(iid,m['state'].get('oi_as_of'),m['state'].get('oi_base'),m['state'].get('oi_received_at') or now)
                detector.remember_context(now)
            loaded=now
        from engine.p39.priority import hot_markets
        def hot_ids():return hot_markets(now)
        if now==loaded:hot=await asyncio.to_thread(hot_ids)
        due={iid:visited.get(iid,0) for iid in detector.catalog if due_at(iid,visited.get(iid,0),now,HOT_MS if iid in hot else ORDINARY_MS)
            or (detector.catalog[iid]['state'].get('oi_as_of') or 0)>consumed_oi.get(iid,0) and 0<=now-(detector.catalog[iid]['state'].get('oi_as_of') or 0)<=90000}
        started=time.perf_counter()
        if due:discovery_progress['details']={**discovery_progress['details'],'scan_started_at':now,'scan_in_progress':True}
        events=await asyncio.to_thread(detector.evaluate_since,due,now) if due else []
        evaluated=time.perf_counter()
        if due:
            with detector.lock:meta={k:dict(v) for k,v in detector.catalog.items()}
            delivery={}
            count=await asyncio.to_thread(persist,events,meta,now,detector.config,delivery=delivery)
            for iid in due:
                visited[iid]=now;consumed_oi[iid]=meta[iid]['state'].get('oi_as_of') or 0
            last_progress={'scan_started_at':now,'scan_finished_at':int(time.time()*1000),'scan_markets':len(due),'event_delivery':delivery,'replayed_events':sum(bool(f.get('replayed')) for _,f,_ in events),'ordinary_interval_ms':ORDINARY_MS,'hot_interval_ms':HOT_MS}
            discovery_progress['details']={**discovery_progress['details'],**last_progress,'scan_in_progress':False}
            timings.append(((evaluated-started)*1000,(time.perf_counter()-evaluated)*1000))
            if count:analysis_wakeup.notify()
        if now-tracked>=HOT_MS:
            await asyncio.to_thread(refresh_tracking,now,detector.config);tracked=now
        if now-reported<HEARTBEAT_MS:
            await asyncio.sleep(15);continue
        snapshot=await asyncio.to_thread(discovery_health_snapshot,detector)
        now=snapshot['snapshot_at'];meta=snapshot['meta'];fresh=snapshot['fresh']
        ready=snapshot['ready'];ready15=snapshot['ready15'];coverage=snapshot['coverage']
        eligible=snapshot['eligible'];pending=snapshot['pending']
        def percentile(column,p):
            values=sorted(x[column] for x in timings);return round(values[min(len(values)-1,int(len(values)*p))],2) if values else None
        latency={'evaluate_p50_ms':percentile(0,.5),'evaluate_p95_ms':percentile(0,.95),'persist_p50_ms':percentile(1,.5),'persist_p95_ms':percentile(1,.95),'samples':len(timings)}
        healthy=bool(eligible) and all(v['ready']/v['eligible']>=detector.config['breadth_coverage'] for v in coverage.values()) and not any(p['field_status'] in ('stale','missing','source_time_unknown') for p in pending)
        health_state='ok' if healthy else 'partial' if ready else 'warming' if fresh else 'delayed'
        discovery_progress.update(state=health_state,details={'snapshot_at':now,'latency':latency,**last_progress,'scan_in_progress':False,'markets':len(meta),'fresh_prices':fresh,'ready_5m':ready,'ready_15m':ready15,'window_coverage':coverage,'oi_window_coverage':snapshot['oi_coverage'],'events_updated':count,'triggered_markets':len(events),'suppressed_markets':sum(c=='market_comovement' for _,_,c in events),'visible_trigger_markets':sum(c!='market_comovement' for _,_,c in events),'pending_pool':pending,'rule_version':'p37-v1','price_windows':[5,15]})
        reported=now
        discovery_progress['details']['price_receipts']=snapshot['price_receipts']
        discovery_progress['details']['oi_receipts']=snapshot['oi_receipts']
        if now-oi_probe_at>=ORDINARY_MS:
            from engine.p40.oi_receipts import sample_persisted
            try:
                oi_probe=await asyncio.to_thread(sample_persisted,detector,oi_probe_offset)
                oi_probe_offset=oi_probe['next_offset']
            except Exception as exc:
                oi_probe={'snapshot_at':now,'status':'probe_failed','error':type(exc).__name__,'scope':'OI persisted-vs-memory diagnostic unavailable'}
            oi_probe_at=now
        discovery_progress['details']['oi_persisted_probe']=oi_probe

        await asyncio.sleep(15)

async def run():
    meta,config=await asyncio.to_thread(catalog);detector.catalog=meta;detector.config=config
    await asyncio.to_thread(seed_memory)
    from engine.pd2.native_streams import supervise_stream
    await asyncio.gather(*(supervise_stream('p37_'+name,fn) for name,fn in [('discover',discover),('windows',collect_windows),('analysis',analyze_loop),('discovery_health',discovery_heartbeat)]))


async def discovery_streams():
    """Only 5m candles across the discovery universe, no tick persistence copy."""
    import json,ssl
    from websockets.asyncio.client import connect
    from engine.p40.streams import ReliableClientConnection,ReceiveTimings,StreamLifecycle
    from engine.p37.detector import ingest
    from engine.pd2.native_streams import supervise_stream,save_packet
    from engine.pd2.write_queue import Writer
    async def group(venue):
        tasks={}
        try:
            while True:
                from engine.repository import runtime_config
                enabled=(await asyncio.to_thread(runtime_config)).collector_enabled
                ids=sorted(iid for iid,m in detector.catalog.items() if m['venue']==venue) if enabled else []
                wanted=[tuple(ids[n:n+128]) for n in range(0,len(ids),128)]
                from engine.p40.receivers import reconcile
                await reconcile(tasks,wanted,lambda chunk:supervised_receiver(venue,chunk))
                await asyncio.sleep(30)
        finally:
            for task in tasks.values():task.cancel()
            await asyncio.gather(*tasks.values(),return_exceptions=True)
    async def supervised_receiver(venue,ids):
        progress={'stable':False}
        await supervise_stream('discovery:'+venue+':'+str(ids[0]),lambda:receive(venue,ids,progress),stable=lambda:progress['stable'])
    async def receive(venue,ids,progress):
        progress['stable']=False
        role='discovery_receiver:'+venue+':'+str(ids[0])
        lifecycle=StreamLifecycle(role,venue,'kline_5m',[iid.rsplit(':',1)[-1].lower()+'@kline_5m' for iid in ids])
        try:await receive_connected(venue,ids,lifecycle,progress)
        except asyncio.CancelledError:
            lifecycle.end('intentional_cancel');raise
        except Exception as exc:
            lifecycle.end('failure',exc)
            from engine.p38.lifecycle import thread
            await thread(heartbeat,role,'reconnecting',{'reason':type(exc).__name__,'lifecycle':lifecycle.metrics()})
            raise
        finally:lifecycle.end('receiver_completed')
    async def receive_connected(venue,ids,lifecycle,progress):
        base='wss://fstream.binance.com/market/stream' if venue=='binance' else 'wss://stream.binance.com:443/stream'
        async with connect(base,ssl=ssl.create_default_context(),max_size=4*1024*1024,create_connection=ReliableClientConnection) as ws:
            lifecycle.attach(ws);lifecycle.connected()
            symbols=[iid.rsplit(':',1)[-1].lower()+'@kline_5m' for iid in ids]
            for n in range(0,len(symbols),100):
                await ws.send(json.dumps({'method':'SUBSCRIBE','params':symbols[n:n+100],'id':n+1}));await asyncio.sleep(.25)
            async def persist_closed(item):await asyncio.to_thread(save_packet,venue,*item)
            from engine.pd2.native_streams import combine_packets
            from engine.pd2.write_queue import failure_directory
            writer=Writer(persist_closed,256,combine_packets,failure_path=failure_directory('discovery:'+venue+':'+str(ids[0])));writer.task=asyncio.create_task(writer.run())
            from engine.p38.loop_health import LoopHealth
            health=LoopHealth();received_at=None;timings=ReceiveTimings()
            async def refresh_health():
                if received_at is not None:
                    from engine.p38.lifecycle import thread
                    await thread(heartbeat,'discovery_receiver:'+venue+':'+str(ids[0]),'connected',{'last_receive_at':received_at,'write_queue':writer.metrics(),'loop_lag':health.metrics(),'receive_timings':timings.metrics(),'markets':len(ids),'lifecycle':lifecycle.metrics()})
            from engine.pd2.stream_maintenance import maintain
            maintenance=asyncio.create_task(maintain(refresh_health))
            try:
                while True:
                    for task in (writer.task,maintenance):
                        if task.done():await task;raise RuntimeError('discovery_background_exited')
                    try:raw=await asyncio.wait_for(ws.recv(),20)
                    except asyncio.TimeoutError:continue
                    packet=json.loads(raw)
                    if 'code' in packet:raise ValueError('discovery_subscription_rejected')
                    now=int(time.time()*1000);received_at=now;timings.received(packet,now)
                    if timings.stable(now):progress['stable']=True
                    started=time.perf_counter();ingest(venue,packet,now,lifecycle.connection_id);timings.record('ingest_ms',started)
                    row=packet.get('data',packet)
                    if row.get('k',{}).get('x'):
                        from engine.p40.streams import measured_writer_put
                        await measured_writer_put(ws,writer,(packet,now),timings)
            except Exception as exc:
                lifecycle.pipeline_snapshot(last_receive_at=received_at,receive_timings=timings.metrics(),write_queue=writer.metrics(),loop_lag=health.metrics())
                lifecycle.end('failure',exc)
                raise
            finally:
                maintenance.cancel();await asyncio.gather(maintenance,return_exceptions=True)
                try:await writer.close()
                finally:await health.close()
    await asyncio.gather(group('binance'),group('binance_spot'))
