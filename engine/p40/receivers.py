"""Retain unchanged subscriptions, including the independent global feed."""
import asyncio

async def reconcile(tasks,wanted,start):
    wanted=set(wanted)
    removed=[key for key,task in tasks.items() if key not in wanted or task.done()]
    for key in removed:tasks[key].cancel()
    await asyncio.gather(*(tasks[key] for key in removed),return_exceptions=True)
    for key in removed:del tasks[key]
    for key in sorted(wanted):
        if key not in tasks:tasks[key]=asyncio.create_task(start(key))
