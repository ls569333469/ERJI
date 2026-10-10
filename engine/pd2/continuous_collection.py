"""Bounded, completion-driven dispatch with per-item failure isolation."""
import asyncio

async def dispatch(choose, execute, enabled, concurrency,maintenance=None,idle_seconds=1):
    pending={}
    try:
        while await enabled():
            if maintenance and maintenance.done():await maintenance
            for key in await choose(concurrency-len(pending)) if len(pending)<concurrency else []:
                if key not in pending:pending[key]=asyncio.create_task(execute(key))
            if not pending:
                await asyncio.sleep(idle_seconds);continue
            done,_=await asyncio.wait(pending.values(),timeout=1,return_when=asyncio.FIRST_COMPLETED)
            for key,task in list(pending.items()):
                if task in done:
                    del pending[key]
                    # execute owns provider errors; infrastructure failures must
                    # reach the supervisor, never become successful checkpoints.
                    await task
    finally:
        for task in pending.values():task.cancel()
        await asyncio.gather(*pending.values(),return_exceptions=True)
