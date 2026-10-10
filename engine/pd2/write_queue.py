"""Bounded fact persistence. Failure is retained and propagated to the producer."""
import asyncio
import time
import json,hashlib,os,uuid,threading
from pathlib import Path
from dataclasses import asdict
from decimal import Decimal
from collections import deque
from copy import deepcopy
from contextlib import asynccontextmanager

def failure_directory(scope):
    return Path(__file__).resolve().parents[2]/'.runtime/failed-facts'/hashlib.sha256(scope.encode()).hexdigest()

def encode(item):
    from engine.adapters.base import Batch
    if isinstance(item,Batch):return {'type':'Batch','value':encode(asdict(item))}
    if isinstance(item,Decimal):return {'type':'Decimal','value':str(item)}
    if isinstance(item,dict):return {'type':'dict','value':{k:encode(v) for k,v in item.items()}}
    if isinstance(item,(tuple,list)):return {'type':'list','value':[encode(v) for v in item]}
    return {'type':'scalar','value':item}

def decode(item):
    kind=item['type'];value=item['value']
    if kind=='Batch':
        from engine.adapters.base import Batch
        return Batch(**decode(value))
    if kind=='Decimal':return Decimal(value)
    if kind=='dict':return {k:decode(v) for k,v in value.items()}
    if kind=='list':return [decode(v) for v in value]
    if kind=='scalar':return value
    raise ValueError('unknown retained fact format')

class Writer:
    def __init__(self, write, capacity=64, combine=None, batch_size=32, failure_path=None):
        self.write=write;self.queue=asyncio.Queue(capacity);self.task=None;self.renewal=None;self.combine=combine;self.batch_size=batch_size
        self.accepted=0;self.committed=0;self.failed=0;self.high_water=0
        self.write_ms=deque(maxlen=300);self.put_wait_ms=deque(maxlen=300);self.queue_wait_ms=deque(maxlen=300)
        self.retries=0;self.retained=0;self.replayed=0;self.inflight=[]
        self.failure_path=Path(failure_path) if failure_path else None
        self.failure_file=self.failure_path/(uuid.uuid4().hex+'.json') if self.failure_path else None
        self.retention_lock=threading.Lock()

    async def retain(self):
        # Snapshot the asyncio queue on its owning loop before disk I/O.
        items=deepcopy([r[0] for r in self.inflight]+[r[0] for r in list(self.queue._queue)])
        if not items or self.failure_file is None:return
        await asyncio.to_thread(self.save_retained,items)

    def save_retained(self,items):
        with self.retention_lock:
            if len(items)<self.retained:return
            self.failure_path.mkdir(parents=True,exist_ok=True)
            tmp=self.failure_file.with_suffix('.tmp')
            with tmp.open('w',encoding='utf8') as f:
                json.dump({'version':1,'items':[encode(i) for i in items]},f,ensure_ascii=False)
                f.flush();os.fsync(f.fileno())
            tmp.replace(self.failure_file);self.retained=len(items)

    async def replay(self):
        if not self.failure_path:return
        from engine.db_retry import retry_write
        from engine.p38.lifecycle import settle
        for path in sorted(self.failure_path.glob('*.json')):
            data=await asyncio.to_thread(lambda:json.loads(path.read_text('utf8')))
            if data.get('version')!=1:raise ValueError('unknown retained fact version')
            for item in data['items']:
                value=decode(item)
                await settle(retry_write(lambda:self.write(value),self.retried))
                self.replayed+=1
            await asyncio.to_thread(path.unlink)

    def retried(self):self.retries+=1

    async def renew_collections(self):
        from engine.pd2.collection_schedule import update_claims
        from engine.p39.timing import COLLECTION_RENEW_SECONDS
        while True:
            # Snapshot accepted records on the owning loop. A freed HTTP slot
            # is not a committed OI point; queued/in-flight writes retain their
            # original owner until persistence and ACK have finished.
            claims={}
            for item,_ in self.inflight+list(self.queue._queue):
                for native,owner in getattr(item,'metadata',{}).get('collection_claims',{}).items():
                    claims.setdefault((item.venue,owner),set()).add(native)
            for (venue,owner),natives in claims.items():
                await asyncio.to_thread(update_claims,venue,sorted(natives),owner)
            await asyncio.sleep(COLLECTION_RENEW_SECONDS)
    async def run(self):
        await self.replay()
        while True:
            records=[await self.queue.get()]
            if self.combine:
                while len(records)<self.batch_size and not self.queue.empty():records.append(self.queue.get_nowait())
            items=[r[0] for r in records]
            self.inflight=records
            self.queue_wait_ms.extend((time.monotonic()-r[1])*1000 for r in records)
            try:
                from engine.p38.lifecycle import settle
                started=time.monotonic()
                from engine.db_retry import retry_write
                value=self.combine(items) if self.combine else items[0]
                await settle(retry_write(lambda:self.write(value),self.retried))
                self.write_ms.append(round((time.monotonic()-started)*1000,2))
                self.committed+=len(items)
                self.inflight=[]
                for _ in items:self.queue.task_done()
            except BaseException:
                self.failed+=len(items)
                await self.retain()
                raise
    async def put(self,item):
        if self.task.done():await self.task
        if self.renewal and self.renewal.done():await self.renewal
        started=time.monotonic()
        pending=asyncio.create_task(self.queue.put((deepcopy(item),started)))
        try:
            await asyncio.wait((pending,self.task,self.renewal) if self.renewal else (pending,self.task),return_when=asyncio.FIRST_COMPLETED)
            if self.task.done():await self.task
            if self.renewal and self.renewal.done():await self.renewal
            await pending
        finally:
            if not pending.done():pending.cancel()
            elif not pending.cancelled() and pending.exception() is None:
                self.accepted+=1;self.high_water=max(self.high_water,self.queue.qsize())
                self.put_wait_ms.append((time.monotonic()-started)*1000)
            if self.task.done() and self.inflight:await self.retain()
    async def drain(self):
        joined=asyncio.create_task(self.queue.join())
        try:
            await asyncio.wait((joined,self.task,self.renewal) if self.renewal else (joined,self.task),return_when=asyncio.FIRST_COMPLETED)
            if self.task.done():await self.task
            if self.renewal and self.renewal.done():await self.renewal
            await joined
        finally:
            if not joined.done():joined.cancel()

    async def close(self):
        from engine.p38.lifecycle import settle
        try:await settle(self.drain())
        finally:
            if self.inflight or not self.queue.empty():await self.retain()
            self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)

    def metrics(self):
        values=sorted(self.write_ms)
        def p95(rows):
            values=sorted(rows);return round(values[min(len(values)-1,int(len(values)*.95))],2) if values else None
        records=self.inflight+list(self.queue._queue)
        oldest=max((time.monotonic()-started for _,started in records),default=0)*1000
        return dict(accepted=self.accepted,committed=self.committed,failed=self.failed,pending=self.queue.qsize(),inflight=len(self.inflight),oldest_uncommitted_ms=round(oldest,2),retained=self.retained,replayed=self.replayed,retries=self.retries,high_water=self.high_water,write_p95_ms=p95(self.write_ms),put_wait_p95_ms=p95(self.put_wait_ms),queue_wait_p95_ms=p95(self.queue_wait_ms))

@asynccontextmanager
async def writing(write,capacity=64,combine=None,failure_path=None):
    writer=Writer(write,capacity,combine,failure_path=failure_path);writer.task=asyncio.create_task(writer.run())
    renewal=asyncio.create_task(writer.renew_collections())
    writer.renewal=renewal
    try:
        yield writer
        await writer.drain()
    finally:
        # Finish accepted facts on normal shutdown/cancellation. A DB exception
        # propagates, allowing REST recovery rather than falsely acknowledging.
        try:await writer.close()
        finally:
            renewal.cancel();await asyncio.gather(renewal,return_exceptions=True)


def combine_batches(items):
    from engine.adapters.base import Batch
    batch=Batch(items[0].venue,candle_only=all(x.candle_only for x in items))
    for item in items:
        if item.venue!=batch.venue:raise ValueError('mixed_venue_write')
        for field in ('instruments','states','candles','oi','funding','raw','errors'):
            getattr(batch,field).extend(getattr(item,field))
        claims={**batch.metadata.get('collection_claims',{}),**item.metadata.get('collection_claims',{})}
        symbols={**batch.metadata.get('collection_symbols',{}),**item.metadata.get('collection_symbols',{})}
        batch.metadata.update(item.metadata)
        if claims:batch.metadata['collection_claims']=claims
        if symbols:batch.metadata['collection_symbols']=symbols
    return batch
