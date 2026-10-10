"""Measure scheduling delay independently from WebSocket arrival intervals."""
import asyncio,time
from collections import deque

class LoopHealth:
    def __init__(self):self.delays=deque(maxlen=300);self.task=asyncio.create_task(self.run())
    async def run(self):
        while True:
            due=time.monotonic()+1
            await asyncio.sleep(1)
            self.delays.append(round(max(0,time.monotonic()-due)*1000,2))
    def metrics(self):
        values=sorted(self.delays)
        return {'samples':len(values),'p95_ms':values[min(len(values)-1,int(len(values)*.95))] if values else None,'max_ms':max(values) if values else None}
    async def close(self):
        self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
