import argparse, asyncio, logging, signal
from engine.adapters.base import public_error
from engine.adapters.cex import LinearPerpAdapter
from engine.adapters.hyperliquid import HyperliquidAdapter
from engine.adapters.binance_universe import BinanceUniverseAdapter
from engine.adapters.expanded_markets import BinanceCashAdapter,OkxAdapter
from engine.adapters.wallet import WalletAdapter
from engine.repository import bootstrap,runtime_config,save_batch,save_failure,heartbeat,candle_targets
log=logging.getLogger("erji.worker")

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
        await asyncio.gather(run_candles(once),run_signals(once),run_pd2(once),run_supplements(once),run_expanded(once))
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
    initialized=set()
    try:
        while True:
            config=await asyncio.to_thread(runtime_config)
            await asyncio.to_thread(heartbeat,"live","collecting" if config.collector_enabled else "paused")
            if config.collector_enabled:
                async def source_task(adapter):
                    try:
                        if adapter.venue=="binance":
                            batch=await adapter.collect(config)
                        else:
                            batch=await adapter.collect(config,history=True)
                        await asyncio.to_thread(save_batch,batch)
                        if batch.states and not batch.errors: initialized.add(adapter.venue)
                        log.info("%s: %s markets, %s gaps",adapter.venue,len(batch.states),len(batch.errors))
                    except Exception as error:
                        await asyncio.to_thread(save_failure,adapter.venue,public_error(error))
                        log.warning("%s unavailable: %s",adapter.venue,type(error).__name__)
                await asyncio.gather(*(source_task(a) for a in adapters))
            await asyncio.to_thread(heartbeat,"live","idle" if config.collector_enabled else "paused")
            if once: break
            # Wait after each complete cycle: never spawn overlapping collections.
            await asyncio.sleep(config.collector_interval_seconds)
    finally:
        await asyncio.gather(*(a.close() for a in adapters))

async def run_candles(once=False):
    adapter=BinanceUniverseAdapter()
    try:
        while True:
            config=await asyncio.to_thread(runtime_config)
            targets=await asyncio.to_thread(candle_targets) if config.collector_enabled else []
            total=0;gaps=0
            for offset in range(0,len(targets),8):
                current=await asyncio.to_thread(runtime_config)
                if not current.collector_enabled: break
                from engine.pd2.tasks import claim,finish
                group=targets[offset:offset+8]
                lease=await asyncio.to_thread(claim,"candles",str(group),120000)
                if not lease:continue
                try:
                    batch=await adapter.collect_candles(group)
                    await asyncio.to_thread(save_batch,batch)
                    await asyncio.to_thread(finish,lease,"partial" if batch.errors else None)
                except Exception as e:
                    await asyncio.to_thread(finish,lease,type(e).__name__)
                    gaps+=len(group);continue
                total+=len(batch.candles);gaps+=len(batch.errors)
                await asyncio.to_thread(heartbeat,"jobs","collecting",{"kind":"candle_history","completed_requests":offset+8,"total_requests":len(targets),"gaps":gaps})
            await asyncio.to_thread(heartbeat,"jobs","idle" if config.collector_enabled else "paused",{"kind":"candle_history","bars_updated":total,"gaps":gaps})
            log.info("candle worker: %s bars, %s gaps",total,gaps)
            if once: break
            await asyncio.sleep(60)
    finally:
        await adapter.close()

async def run_signals(once=False):
    from engine.signal_service import scan_signals
    while True:
        try:
            stats=await asyncio.to_thread(scan_signals)
            log.info("structure signals: %s",stats)
        except Exception as error:
            await asyncio.to_thread(heartbeat,"signals","error",{"reason":type(error).__name__})
            log.warning("structure scan failed: %s",type(error).__name__)
        if once:return
        await asyncio.sleep(45)


async def run_pd2(once=False):
    from engine.pd2.execution import ExecutionAdapter
    from engine.pd2.service import refresh_plans
    from engine.models import Episode,Campaign,Instrument
    from engine.db import Session
    from sqlalchemy import select
    adapter=ExecutionAdapter()
    try:
        while True:
            with Session() as s:
                assets=list(dict.fromkeys(s.scalars(select(Campaign.asset_id).join(Episode).where(Episode.phase.notin_(["invalidated","ended"])))))
                groups={asset:list(s.scalars(select(Instrument.id).where(Instrument.asset_id==asset))) for asset in assets}
            from engine.pd2.market_selection import select_main
            cfg=await asyncio.to_thread(runtime_config)
            for asset,ids in groups.items():
                await asyncio.gather(*(adapter.collect(i) for i in ids),return_exceptions=True)
                try:
                    await asyncio.to_thread(select_main,asset,cfg)
                    await asyncio.to_thread(refresh_plans,ids)
                except Exception as error:await asyncio.to_thread(heartbeat,"decisions","error",{"asset":asset,"reason":type(error).__name__})
            if once:return
            await asyncio.sleep(5)
    finally:await adapter.close()

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
    adapters=[BinanceCashAdapter(),BinanceCashAdapter(alpha=True),WalletAdapter(),OkxAdapter()]
    try:
        while True:
            config=await asyncio.to_thread(runtime_config)
            if config.collector_enabled:
                async def source(adapter):
                    try:
                        batch=await adapter.collect(config)
                        await asyncio.to_thread(save_batch,batch)
                    except Exception as e:await asyncio.to_thread(save_failure,adapter.venue,public_error(e))
                await asyncio.gather(*(source(a) for a in adapters))
            if once:return
            await asyncio.sleep(300)
    finally:await asyncio.gather(*(a.close() for a in adapters))


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--role",choices=["live","jobs","notify","streams"],default="live");parser.add_argument("--once",action="store_true")
    args=parser.parse_args();logging.basicConfig(level=logging.INFO,format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try: asyncio.run(run(args.role,args.once))
    except KeyboardInterrupt: pass
