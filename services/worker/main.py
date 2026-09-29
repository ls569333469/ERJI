import argparse, asyncio, logging, signal
from engine.adapters.base import public_error
from engine.adapters.cex import LinearPerpAdapter
from engine.adapters.hyperliquid import HyperliquidAdapter
from engine.adapters.binance_universe import BinanceUniverseAdapter
from engine.adapters.expanded_markets import BinanceCashAdapter,OkxAdapter
from engine.adapters.wallet import WalletAdapter
from engine.repository import bootstrap,runtime_config,save_batch,save_failure,heartbeat,candle_targets
log=logging.getLogger("erji.worker")

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

async def run(role,once=False):
    from engine.pd2.scheduling import role_lock
    lease=role_lock(role)
    try:
        await run_locked(role,once)
    finally:lease.close()

async def run_locked(role,once=False):
    bootstrap()
    if role=="streams":
        from engine.pd2.native_streams import run_streams
        await run_streams();return
    if role=="jobs":
        await asyncio.gather(*(supervise(name,fn,once) for name,fn in (("candles",run_candles),("signals",run_signals),("decisions",run_pd2),("projection",run_projection),("supplements",run_supplements),("expanded",run_expanded))))
        return
    if role=="notify":
        from engine.pd2.notifications import dispatch_once
        while True:
            config=await asyncio.to_thread(runtime_config)
            result=await dispatch_once() if config.notifications.enabled else {"status":"disabled"}
            await asyncio.to_thread(heartbeat,role,result["status"],result)
            if once:return
            await asyncio.sleep(10)
    adapters=[BinanceUniverseAdapter(),HyperliquidAdapter(),LinearPerpAdapter("aster")]
    async def source_loop(adapter):
        from engine.pd2.collection_schedule import acknowledge_batch
        delay=60 if adapter.venue=="hyperliquid" else 5
        failures=0
        try:
            while True:
                started=asyncio.get_running_loop().time()
                try:
                    config=await asyncio.to_thread(runtime_config)
                    if config.collector_enabled:
                        batch=await adapter.collect(config) if adapter.venue=="binance" else await adapter.collect(config,history=False)
                        if not batch.metadata.get("idle"):
                            await asyncio.to_thread(save_batch,batch)
                            await asyncio.to_thread(acknowledge_batch,batch)
                        await asyncio.to_thread(heartbeat,"live_"+adapter.venue,"partial" if batch.errors else "ok",{"updated":len(batch.states),"gaps":len(batch.errors),"schedule":"per_asset_deadlines"})
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

async def run_candles(once=False):
    from engine.pd2.collection_schedule import acknowledge
    async def source(factory):
        adapter=factory()
        try:
            while True:
                cfg=await asyncio.to_thread(runtime_config)
                targets=await asyncio.to_thread(candle_targets,adapter.venue) if cfg.collector_enabled else []
                if targets:
                    batch=await adapter.collect_candles(targets)
                    await asyncio.to_thread(save_batch,batch)
                    # A market is acknowledged only after every requested timeframe succeeds.
                    success={(r['instrument_id'],r['resolution']) for r in batch.candles}
                    natives={t[1] for t in targets if all((x[0],x[2]) in success for x in targets if x[0]==t[0])}
                    if natives:await asyncio.to_thread(acknowledge,adapter.venue,natives,'candles')
                    await asyncio.to_thread(heartbeat,'candles_'+adapter.venue,'partial' if batch.errors else 'ok',{'markets':len(set(t[0] for t in targets)),'bars_updated':len(batch.candles),'gaps':len(batch.errors)})
                if once:return
                await asyncio.sleep(5)
        finally:await adapter.close()
    await asyncio.gather(*(supervise('candles_'+name,lambda single,factory=factory:source(factory),once) for name,factory in [('binance',BinanceUniverseAdapter),('spot',BinanceCashAdapter),('alpha',lambda:BinanceCashAdapter(alpha=True))]))

async def run_signals(once=False):
    from engine.signal_service import scan_signals
    from engine.repository import read_events
    from engine.adapters.base import now_ms
    from engine.db import Session
    from engine.models import UserPreference
    with Session() as s:
        row=s.get(UserPreference,'structure_scan_cursor');cursor=(row.payload if row else {}).get('event_id',0)
    full_at=0
    while True:
        events=await asyncio.to_thread(read_events,cursor,2000)
        changed={iid for event in events if event['kind']=='candle_update' for iid in event['payload'].get('closed_instruments',[])}
        if now_ms()-full_at>=300000:
            await asyncio.to_thread(scan_signals);full_at=now_ms()
        elif changed:
            await asyncio.to_thread(scan_signals,sorted(changed))
        if events:
            cursor=events[-1]['id']
            with Session.begin() as s:s.merge(UserPreference(key='structure_scan_cursor',updated_at=now_ms(),payload={'event_id':cursor}))
        if once:return
        await asyncio.sleep(2)


async def run_pd2(once=False):
    from engine.pd2.execution import ExecutionAdapter
    from engine.pd2.service import refresh_plans
    from engine.models import Episode,Campaign,Instrument,CurrentSignal
    from engine.db import Session
    from sqlalchemy import select
    adapter=ExecutionAdapter();pending={};due_at={};groups={};primary_ids=set();checked=0;levels={}
    from engine.adapters.base import now_ms
    async def assess(asset,ids,cfg):
        from engine.pd2.market_selection import select_main
        try:
            await asyncio.gather(*(adapter.collect(i) for i in ids if i in primary_ids),return_exceptions=True)
            await asyncio.to_thread(select_main,asset,cfg)
            await asyncio.to_thread(refresh_plans,ids)
        except asyncio.CancelledError:raise
        except Exception as error:
            await asyncio.to_thread(heartbeat,"decisions","error",{"asset":asset,"reason":type(error).__name__})
    try:
        while True:
            now=now_ms()
            if now-checked>=10000:
                cfg=await asyncio.to_thread(runtime_config)
                from engine.pd2.universe_policy import membership,PRIMARY,primary_eligible
                with Session() as s:
                    assets=set(s.scalars(select(Campaign.asset_id).join(Episode).where(Episode.phase.notin_(["invalidated","ended"]))))
                    catalog=list(s.scalars(select(Instrument)))
                    groups={asset:[i.id for i in catalog if i.asset_id==asset and i.venue in PRIMARY] for asset in assets}
                    aliases,admitted=membership(s)
                    primary_ids={i.id for i in catalog if primary_eligible(i)}
                    levels={r.asset_id:3000 for r in s.scalars(select(CurrentSignal).where(CurrentSignal.action.in_(['trial','participate','paused','expired'])))}
                checked=now
            for asset,task in list(pending.items()):
                if task.done():
                    await task;pending.pop(asset)
                    due_at[asset]=now+levels.get(aliases.get(asset,asset),30000)
            ready=sorted((a for a in groups if a not in pending and due_at.get(a,0)<=now),key=lambda a:(due_at.get(a,0),a))
            for asset in ready[:max(0,4-len(pending))]:
                pending[asset]=asyncio.create_task(assess(asset,groups[asset],cfg))
            if once:
                # Bounded one-shot for diagnostics, with no whole-universe HTTP burst.
                await asyncio.gather(*pending.values());return
            await asyncio.sleep(.5)
    finally:
        for task in pending.values():task.cancel()
        await asyncio.gather(*pending.values(),return_exceptions=True)
        await adapter.close()

async def run_projection(once=False):
    from engine.pd2.service import decision_list
    while True:
        await asyncio.to_thread(decision_list)
        if once:return
        await asyncio.sleep(2)


async def run_supplements(once=False):
    from engine.pd2.providers import Supplemental,seed_verified_identity
    from engine.pd2.outcomes import update_outcomes
    await asyncio.to_thread(seed_verified_identity)
    adapter=Supplemental()
    try:
        while True:
            try:
                await adapter.cmc()
                await adapter.bykaranteli()
                await asyncio.to_thread(update_outcomes)
                await asyncio.to_thread(heartbeat,"supplements","ok")
            except Exception as error:
                await asyncio.to_thread(heartbeat,"supplements","error",{"reason":type(error).__name__})
            if once:return
            await asyncio.sleep(60)
    finally:await adapter.close()



async def run_expanded(once=False):
    from engine.pd2.collection_schedule import acknowledge
    adapters=[BinanceCashAdapter(),BinanceCashAdapter(alpha=True),WalletAdapter(),OkxAdapter()]
    async def source(adapter):
        try:
            while True:
                try:
                    config=await asyncio.to_thread(runtime_config)
                    if config.collector_enabled:
                        batch=await adapter.collect(config,history=adapter.venue=='wallet_rising')
                        if batch.metadata.get("idle"):
                            if once:return
                            await asyncio.sleep(5);continue
                        await asyncio.to_thread(save_batch,batch)
                        if batch.metadata.get('wallet_completed'):
                            await asyncio.to_thread(acknowledge,adapter.venue,batch.metadata['wallet_completed'],'wallet_assets')
                        if adapter.venue in ('binance_spot','binance_alpha'):
                            await asyncio.to_thread(acknowledge,adapter.venue,[r['instrument_id'].rsplit(':',1)[-1] for r in batch.states],'price')
                except Exception as error:
                    try:await asyncio.to_thread(save_failure,adapter.venue,public_error(error))
                    except Exception:log.warning('source status unavailable')
                if once:return
                await asyncio.sleep(5 if adapter.venue in ('binance_spot','binance_alpha','wallet_rising') else 300)
        finally:await adapter.close()
    await asyncio.gather(*(source(a) for a in adapters))


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--role",choices=["live","jobs","notify","streams"],default="live");parser.add_argument("--once",action="store_true")
    args=parser.parse_args();logging.basicConfig(level=logging.INFO,format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try: asyncio.run(run(args.role,args.once))
    except KeyboardInterrupt: pass
