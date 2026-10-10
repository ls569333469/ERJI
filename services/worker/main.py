import argparse, asyncio, logging, signal
from engine.adapters.base import public_error
from engine.adapters.binance_universe import BinanceUniverseAdapter
from engine.adapters.expanded_markets import BinanceCashAdapter
from engine.adapters.wallet import WalletAdapter
from engine.repository import bootstrap,runtime_config,save_batch,save_failure,heartbeat,candle_targets
log=logging.getLogger("erji.worker")

def binance_bridge_active():
    from engine.adapters.base import now_ms
    from engine.db import Session
    from engine.models import WorkerHeartbeat

    with Session() as session:
        row=session.get(WorkerHeartbeat,"bridge_binance")
        return bool(row and row.state in ("ok","partial") and now_ms()-row.updated_at<120000)

async def supervise(name, fn, once):
    while True:
        try:
            await fn(once)
            return
        except asyncio.CancelledError:raise
        except Exception as error:
            log.exception("%s loop failed",name)
            try:await asyncio.to_thread(heartbeat,name,"error",{"reason":type(error).__name__})
            except Exception:log.warning("health database unavailable")
            if once:raise
            await asyncio.sleep(5)

async def independent_io(fn):
    """Keep collection I/O responsive while legacy jobs do synchronous DB work.

    Adapters are created inside the dedicated loop, never shared across loops.
    All workers still use the same persisted budgets and role owner.
    """
    import threading
    loop=asyncio.new_event_loop()
    thread=threading.Thread(target=loop.run_forever,name='erji-collection-io',daemon=True)
    thread.start()
    future=asyncio.run_coroutine_threadsafe(fn(),loop)
    try:
        return await asyncio.wrap_future(future)
    finally:
        async def drain():
            tasks=[t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            await loop.shutdown_asyncgens()
            await loop.shutdown_default_executor()
        await asyncio.shield(asyncio.wrap_future(asyncio.run_coroutine_threadsafe(drain(),loop)))
        loop.call_soon_threadsafe(loop.stop)
        await asyncio.to_thread(thread.join)
        loop.close()

async def run(role,once=False):
    from engine.p38.startup import manifest
    import json
    log.info("startup %s",json.dumps(manifest(role),ensure_ascii=False))
    if role=='budget':
        from engine.pd2.budget_owner import serve
        await asyncio.to_thread(serve);return
    from engine.pd2.scheduling import role_lock
    lease=role_lock(role)
    import engine.db as database
    database.role_owner=lease
    token=database.owner_context.set(lease)
    body=asyncio.create_task(run_locked(role,once))
    async def watch_owner():
        while True:
            await asyncio.sleep(2)
            from engine.p38.lifecycle import thread
            await thread(lease.check)
    watcher=asyncio.create_task(watch_owner())
    try:
        done,_=await asyncio.wait((body,watcher),return_when=asyncio.FIRST_COMPLETED)
        for task in done:await task
    finally:
        body.cancel();watcher.cancel()
        await asyncio.gather(body,watcher,return_exceptions=True)
        database.owner_context.reset(token)
        database.role_owner=None;lease.close()

async def run_locked(role,once=False):
    if role=="binance-bridge":
        # A remote collector must never upgrade or initialize an older production schema.
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from alembic.runtime.migration import MigrationContext
        from engine.db import engine
        from pathlib import Path
        root=Path(__file__).resolve().parents[2]
        cfg=Config(str(root/'alembic.ini'))
        cfg.set_main_option('script_location',str(root/'migrations'))
        with engine.connect() as connection:
            current=set(MigrationContext.configure(connection).get_current_heads())
        if current!=set(ScriptDirectory.from_config(cfg).get_heads()):
            raise RuntimeError("bridge_schema_mismatch: deploy database migrations before connecting bridge")
    bootstrap()
    from engine.p38.startup import manifest
    await asyncio.to_thread(heartbeat,role,'running',manifest(role))
    if role=="binance-bridge":
        await run_binance_bridge(once);return
    if role=="streams":
        from engine.pd2.native_streams import run_streams
        from engine.p37.worker import run as run_observations,discovery_streams
        await run_streams((('observations',run_observations),('discovery_5m',discovery_streams)));return
    if role=="jobs":
        async def collectors():
            await asyncio.gather(*(supervise(name,fn,once) for name,fn in (
                ('candles',run_candles),('derivative_history',run_derivative_history),
                ('history_backfill',run_history_backfill),('expanded',run_expanded))))
        await asyncio.gather(independent_io(collectors),*(supervise(name,fn,once) for name,fn in (
            ('signals',run_signals),('decisions',run_pd2),('projection',run_projection),
            ('coverage',run_coverage),('supplements',run_supplements))))
        return
    if role=="notify":
        from engine.pd2.notifications import dispatch_once
        while True:
            config=await asyncio.to_thread(runtime_config)
            result=await dispatch_once() if config.notifications.enabled else {"status":"disabled"}
            await asyncio.to_thread(heartbeat,role,result["status"],result)
            if once:return
            await asyncio.sleep(10)
    adapters=[BinanceUniverseAdapter()]
    async def source_loop(adapter):
        from engine.pd2.collection_schedule import acknowledge_batch
        delay=5
        failures=0
        try:
            while True:
                started=asyncio.get_running_loop().time()
                try:
                    if adapter.venue=="binance" and await asyncio.to_thread(binance_bridge_active):
                        if once:return
                        await asyncio.sleep(delay);continue
                    config=await asyncio.to_thread(runtime_config)
                    if config.collector_enabled:
                        async def partial(part):
                            await asyncio.to_thread(save_batch,part)
                            await asyncio.to_thread(acknowledge_batch,part)
                        if adapter.venue=="binance":
                            from engine.pd2.write_queue import writing,combine_batches
                            from engine.pd2.write_queue import failure_directory
                            async with writing(partial,32,combine_batches,failure_path=failure_directory('collector:'+adapter.venue)) as writer:
                                batch=await adapter.collect(config,on_batch=writer.put,continuous=not once,write_metrics=writer.metrics)
                        else:batch=await adapter.collect(config,history=False)
                        if not batch.metadata.get("idle"):
                            await asyncio.to_thread(save_batch,batch)
                            await asyncio.to_thread(acknowledge_batch,batch)
                        await asyncio.to_thread(heartbeat,"live_"+adapter.venue,"partial" if batch.errors else "ok",{"updated":len(batch.states)+batch.metadata.get("persisted_states",0),"gaps":len(batch.errors),"schedule":"per_asset_deadlines"})
                    failures=0
                except Exception as error:
                    failures+=1
                    try:await asyncio.to_thread(save_failure,adapter.venue,public_error(error))
                    except Exception:log.warning("health database unavailable")
                    log.warning("%s unavailable: %s",adapter.venue,type(error).__name__)
                if once:return
                elapsed=asyncio.get_running_loop().time()-started
                await asyncio.sleep(max(1,(min(60,2**failures) if failures else delay)-elapsed))
        finally:await adapter.close()
    await asyncio.gather(*(source_loop(a) for a in adapters))

async def run_derivative_history(once=False):
    from engine.pd2.derivative_collection import collect_due
    from engine.pd2.collection_schedule import next_delay
    async def source(kind):
        adapter=BinanceUniverseAdapter()
        try:
            while True:
                config=await asyncio.to_thread(runtime_config)
                if config.collector_enabled:
                    result=await collect_due(adapter,(kind,),continuous=not once)
                    await asyncio.to_thread(heartbeat,'derivative_'+kind,'partial' if result['errors'] else 'ok',result)
                if once:return
                delay=await asyncio.to_thread(next_delay,'binance',kind) if config.collector_enabled else 5
                await asyncio.sleep(delay)
        finally:await adapter.close()
    await asyncio.gather(*(source(kind) for kind in ('funding_history','oi_history')))


async def run_history_backfill(once=False):
    from engine.pd2.history_backfill import collect_one
    adapter=BinanceUniverseAdapter()
    try:
        while True:
            config=await asyncio.to_thread(runtime_config)
            if config.collector_enabled:
                await asyncio.to_thread(heartbeat,'history_backfill','running',{'stage':'bounded_one_page'})
                from engine.pd2.request_budget import request_lane
                token=request_lane.set('cold')
                try:result=await collect_one(adapter)
                finally:request_lane.reset(token)
                await asyncio.to_thread(heartbeat,'history_backfill',result['status'],result)
            if once:return
            await asyncio.sleep(5 if config.collector_enabled and result.get('status')=='progress' else 30)
    finally:await adapter.close()


async def run_binance_bridge(once=False):
    """Local-only Binance USD-M collector writing to the shared ERJI database.

    The process is intentionally separate from the server worker-live role so a
    machine with Binance access can provide only the blocked futures source.
    """
    from engine.pd2.collection_schedule import acknowledge, acknowledge_batch

    adapter=BinanceUniverseAdapter()
    try:
        while True:
            try:
                config=await asyncio.to_thread(runtime_config)
                if config.collector_enabled:
                    batch=await adapter.collect(config)
                    if not batch.metadata.get("idle"):
                        await asyncio.to_thread(save_batch,batch)
                        await asyncio.to_thread(acknowledge_batch,batch)
                    targets=await asyncio.to_thread(candle_targets,adapter.venue)
                    progress=await persist_candle_targets(adapter,targets) if targets else {'bars_updated':0,'gaps':0}
                    await asyncio.to_thread(heartbeat,"bridge_binance","partial" if batch.errors or progress['gaps'] else "ok",{
                        "updated":len(batch.states),"candles":progress['bars_updated'],
                        "gaps":len(batch.errors)+progress['gaps'],
                        "schedule":"per_asset_deadlines","transport":"local_bridge","gap_owner":"p38"})
                    if once and (batch.errors or progress['gaps']):
                        raise RuntimeError("bridge_incomplete_collection")
                else:
                    await asyncio.to_thread(heartbeat,"bridge_binance","disabled",{})
            except Exception as error:
                try:
                    await asyncio.to_thread(save_failure,"binance",public_error(error))
                except Exception:
                    log.warning("binance bridge health database unavailable")
                log.warning("binance bridge unavailable: %s",type(error).__name__)
                # The launcher recreates the SSH tunnel and role lease on failure.
                raise
            if once:return
            await asyncio.sleep(5)
    finally:
        await adapter.close()


async def persist_candle_targets(adapter, targets):
    """Commit each completed timeframe, without waiting for the slowest market.

    Use the shared REST budget; verified Binance families admit eight targets.
    A market's deadline advances only after all its requested frames are saved.
    """
    from engine.pd2.collection_schedule import acknowledge
    required={}
    for iid,native,res,*_ in targets:
        required.setdefault(native,set()).add((iid,res))
    completed=set();acknowledged=set();bars=0;gaps=0;finished=0
    queue=asyncio.Queue()
    for target in targets:queue.put_nowait(target)
    update_lock=asyncio.Lock()
    async def consume():
        nonlocal bars,gaps,finished
        while not queue.empty():
            target=queue.get_nowait()
            from engine.p38.candle_gaps import fetch
            batch=await fetch(adapter,target,'live')
            if batch is None:continue
            async with update_lock:
                from engine.repository import candle_window_ready
                if await asyncio.to_thread(candle_window_ready,target[0],target[2]):
                    completed.add((target[0],target[2]))
                bars+=len(batch.candles);gaps+=len(batch.errors);finished+=1
                native=target[1]
                if native not in acknowledged and required[native]<=completed:
                    await asyncio.to_thread(acknowledge,adapter.venue,[native],'candles')
                    acknowledged.add(native)
                await asyncio.to_thread(heartbeat,'candles_'+adapter.venue,'partial' if gaps else 'ok',{
                    'markets':len(required),'markets_completed':len(acknowledged),
                    'bars_updated':bars,'gaps':gaps,'targets_completed':finished,
                    'targets_total':len(targets),'incremental_persistence':True})
    tasks=[asyncio.create_task(consume()) for _ in range(min(8 if adapter.venue in ('binance','binance_spot') else 4,len(targets)))]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)


    return {"bars_updated":bars,"gaps":gaps,"markets_completed":len(acknowledged),"targets_completed":finished}


async def run_candles(once=False):
    async def source(factory):
        adapter=factory()
        try:
            while True:
                cfg=await asyncio.to_thread(runtime_config)
                bridged=adapter.venue=="binance" and await asyncio.to_thread(binance_bridge_active)
                targets=await asyncio.to_thread(candle_targets,adapter.venue) if cfg.collector_enabled and not bridged else []
                if targets:
                    await persist_candle_targets(adapter,targets)
                if once:return
                await asyncio.sleep(5)
        finally:await adapter.close()
    await asyncio.gather(*(supervise('candles_'+name,lambda single,factory=factory:source(factory),once) for name,factory in [('binance',BinanceUniverseAdapter),('spot',BinanceCashAdapter),('alpha',lambda:BinanceCashAdapter(alpha=True))]))

async def run_signals(once=False):
    from engine.p38.jobs import submit
    from engine.signal_service import _reconciliation_batch
    from engine.repository import read_events
    from engine.adapters.base import now_ms
    from engine.db import Session
    from engine.models import UserPreference
    with Session() as s:
        row=s.get(UserPreference,'structure_scan_cursor');cursor=(row.payload if row else {}).get('event_id',0)
    reconcile_at=0;priority_cursor=0
    while True:
        events=await asyncio.to_thread(read_events,cursor,2000)
        changed={iid for event in events if event['kind']=='candle_update' for iid in event['payload'].get('closed_instruments',[])}
        if changed:
            from engine.p39.pipeline import wake_markets
            await asyncio.to_thread(wake_markets,changed,now_ms(),'closed_candle_or_revision')
            for kind in ('trend','structure'):await asyncio.to_thread(submit,sorted(changed),'closed_candle',work_kind=kind)
        if now_ms()>=reconcile_at:
            # Repair missed events in bounded DB-only batches. This does not call providers.
            from engine.p38.jobs import eligible
            with Session() as s:
                from engine.models import Instrument
                from sqlalchemy import select
                batch=eligible(list(s.scalars(select(Instrument.id))))
            completed=True
            await asyncio.to_thread(submit,batch,'coverage_repair')
            with Session.begin() as s:s.merge(UserPreference(key='structure_reconcile_cursor',updated_at=now_ms(),payload={'instrument_id':None if completed else batch[-1] if batch else None}))
            from engine.pd2.p35_schedule import priority_instruments
            with Session() as s:priority_ids=sorted(priority_instruments(s))
            if priority_ids:
                priority_cursor%=len(priority_ids)
                priority_batch=priority_ids[priority_cursor:priority_cursor+10]
                priority_cursor+=len(priority_batch)
                await asyncio.to_thread(submit,priority_batch,'priority_repair')
            # Discovery has its own event path; this is only missed-event repair.
            reconcile_at=now_ms()+60000
        if events:
            cursor=events[-1]['id']
            with Session.begin() as s:s.merge(UserPreference(key='structure_scan_cursor',updated_at=now_ms(),payload={'event_id':cursor}))
        if once:return
        await asyncio.sleep(30)


async def run_pd2(once=False):
    from engine.p38.jobs import run
    await run(once)

async def run_projection(once=False):
    from engine.p39.pipeline import enabled
    if await asyncio.to_thread(enabled):
        if once:return
        await asyncio.Future()
        return
    from engine.p38.jobs import submit
    from engine.repository import read_events
    from engine.adapters.base import now_ms
    from engine.db import Session
    from engine.models import UserPreference
    with Session() as session:
        saved=session.get(UserPreference,'projection_event_cursor');cursor=(saved.payload if saved else {}).get('event_id',0)
    from engine.models import Campaign
    from sqlalchemy import select
    due={}
    while True:
        events=await asyncio.to_thread(read_events,cursor,2000)
        changed=set()
        for event in events:
            changed.update(event['payload'].get('instruments',[]))
            if event['payload'].get('instrument_id'):changed.add(event['payload']['instrument_id'])
        def targets():
            with Session() as s:return list(s.scalars(select(Campaign.reference_instrument_id).distinct()))
        ids=await asyncio.to_thread(targets)
        now=now_ms()
        oldest=sorted(ids,key=lambda iid:due.get(iid,0))[:2]
        ordered=oldest+[iid for iid in sorted(ids,key=lambda iid:(iid not in changed,due.get(iid,0))) if iid not in oldest]
        for iid in ordered[:10]:
            await asyncio.to_thread(submit,[iid],'projection_repair',work_kind='projection');due[iid]=now_ms()
        if events:cursor=events[-1]['id']
        with Session.begin() as session:
            session.merge(UserPreference(key='projection_event_cursor',updated_at=now_ms(),payload={'event_id':cursor}))
        if once:return
        await asyncio.sleep(1)


async def run_coverage(once=False):
    from engine.adapters.base import now_ms
    from engine.pd2.signal_projection import refresh_coverage
    from engine.pd2.offline_indicators import refresh_one
    while True:
        await asyncio.to_thread(refresh_coverage)
        from engine.pd2.data_health import refresh as refresh_data_health
        await asyncio.to_thread(refresh_data_health)
        history=await asyncio.to_thread(refresh_one,now_ms())
        await asyncio.to_thread(heartbeat,'coverage','ok',{'offline_history':history.get('status')})
        if once:return
        await asyncio.sleep(300)


async def run_supplements(once=False):
    from engine.pd2.binance_facts import BinanceReferenceAdapter
    from engine.pd2.outcomes import update_outcomes
    adapter=BinanceReferenceAdapter()
    try:
        while True:
            try:
                if (await asyncio.to_thread(runtime_config)).collector_enabled:
                    result=await adapter.collect_fx()
                    supply=await adapter.collect_supply()
                else:result={}
                await asyncio.to_thread(update_outcomes)
                await asyncio.to_thread(heartbeat,"supplements","ok" if result and all(v=='fresh' for v in result.values()) else "partial" if result else "disabled",{'fx':result,'supply':supply if result else 'disabled','source':'Binance assetIndex and advisory web metadata'})
            except Exception as error:
                await asyncio.to_thread(heartbeat,"supplements","error",{"reason":type(error).__name__})
            if once:return
            await asyncio.sleep(300)
    finally:await adapter.close()



async def run_expanded(once=False):
    from engine.pd2.collection_schedule import acknowledge
    adapters=[BinanceCashAdapter(),BinanceCashAdapter(alpha=True),WalletAdapter()]
    async def source(adapter):
        async def persist_quote(part):
            await asyncio.to_thread(save_batch,part)
            await asyncio.to_thread(acknowledge,adapter.venue,[r['instrument_id'].rsplit(':',1)[-1] for r in part.states],'volume' if adapter.venue=='binance_spot' else 'price')
        try:
            while True:
                try:
                    config=await asyncio.to_thread(runtime_config)
                    if config.collector_enabled:
                        batch=await adapter.collect(config,history=adapter.venue=='wallet_rising',**({'on_batch':persist_quote} if adapter.venue=='binance_alpha' else {}))
                        if batch.metadata.get("idle"):
                            if once:return
                            await asyncio.sleep(5);continue
                        await asyncio.to_thread(save_batch,batch)
                        if batch.metadata.get('wallet_completed'):
                            await asyncio.to_thread(acknowledge,adapter.venue,batch.metadata['wallet_completed'],'wallet_assets')
                        if adapter.venue in ('binance_spot','binance_alpha'):
                            await asyncio.to_thread(acknowledge,adapter.venue,[r['instrument_id'].rsplit(':',1)[-1] for r in batch.states],'volume' if adapter.venue=='binance_spot' else 'price')
                except Exception as error:
                    try:await asyncio.to_thread(save_failure,adapter.venue,public_error(error))
                    except Exception:log.warning('source status unavailable')
                if once:return
                await asyncio.sleep(5 if adapter.venue in ('binance_spot','binance_alpha','wallet_rising') else 300)
        finally:await adapter.close()
    await asyncio.gather(*(source(a) for a in adapters))


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--role",choices=["live","jobs","notify","streams","binance-bridge","budget"],default="live");parser.add_argument("--once",action="store_true")
    args=parser.parse_args();logging.basicConfig(level=logging.INFO,format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try: asyncio.run(run(args.role,args.once))
    except KeyboardInterrupt: pass
