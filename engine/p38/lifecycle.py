"""Keep an owning coroutine alive until its synchronous work actually exits."""
import asyncio
from contextvars import ContextVar

commit_guard=ContextVar('commit_guard',default=None)

async def settle(awaitable):
    task=asyncio.ensure_future(awaitable)
    cancelled=False
    while not task.done():
        try:await asyncio.shield(task)
        except asyncio.CancelledError:cancelled=True
    result=task.result()
    if cancelled:raise asyncio.CancelledError
    return result

async def thread(fn,*args,**kwargs):
    return await settle(asyncio.to_thread(fn,*args,**kwargs))

async def acquire_thread(fn,cleanup):
    """If cancellation races acquisition, dispose the acquired resource."""
    task=asyncio.create_task(asyncio.to_thread(fn))
    try:return await settle(task)
    except asyncio.CancelledError:
        if task.done() and not task.cancelled() and task.exception() is None:
            await thread(cleanup,task.result())
        raise
