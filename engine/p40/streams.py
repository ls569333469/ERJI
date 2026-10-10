"""Transport compatibility and bounded, per-connection pipeline timings."""
import time
import logging
import os
import threading
import json
import random
from hashlib import sha256
from uuid import uuid4
from collections import deque,Counter
from websockets.frames import Frame,Opcode
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.messages import Assembler

_lifecycle_lock=threading.Lock()
_lifecycles={}

def _history(role):
    if role not in _lifecycles and len(_lifecycles)>=64:_lifecycles.pop(next(iter(_lifecycles)))
    return _lifecycles.setdefault(role,deque(maxlen=16))

class StreamLifecycle:
    """Bounded cross-reconnect history; closing evidence also survives in logs."""
    def __init__(self,role,venue,channel,subscriptions):
        self.role=role;self.start=int(time.time()*1000);self.connected_at=None;self.ended=False
        self.pipeline={};self.connection=None
        self.ident={'connection_id':uuid4().hex,'run_id':os.getenv('ERJI_RUN_ID'),
            'venue':venue,'channel':channel,'subscription_hash':sha256('\n'.join(sorted(subscriptions)).encode()).hexdigest()[:20],
            'started_at':self.start}
        with _lifecycle_lock:
            _history(role)
    @property
    def connection_id(self):return self.ident['connection_id']
    def connected(self):self.connected_at=int(time.time()*1000)
    def attach(self,connection):self.connection=connection
    def transport_snapshot(self):
        snapshot=getattr(self.connection,'diagnostic_snapshot',None)
        return snapshot() if snapshot else None
    def end(self,reason,exc=None,retry_seconds=None):
        if self.ended:return
        self.ended=True
        closes={}
        for direction in ('rcvd','sent'):
            close=getattr(exc,direction,None)
            closes[direction]={'code':getattr(close,'code',None),'reason':str(getattr(close,'reason',''))[:240]}
        row={**self.ident,'connected_at':self.connected_at,'ended_at':int(time.time()*1000),
            'end_reason':reason,'exception':type(exc).__name__ if exc else None,'close':closes,'retry_seconds':retry_seconds}
        row['pipeline']=dict(self.pipeline)
        row['transport']=self.transport_snapshot()
        with _lifecycle_lock:_history(self.role).append(row)
        logging.getLogger('erji.streams').info('stream_lifecycle %s',json.dumps({'role':self.role,**row},ensure_ascii=False))
    def metrics(self):
        with _lifecycle_lock:history=list(_lifecycles.get(self.role,()))
        return {**self.ident,'connected_at':self.connected_at,'recent_closes':history,'transport':self.transport_snapshot(),
            'scope':'current process/run; last 16 closes per receiver; detailed closing records in registered log'}

    def pipeline_snapshot(self,**value):
        with _lifecycle_lock:self.pipeline.update(value)

def retry_delay(backoff):return random.uniform(.8,1.2)*backoff

class ReliableClientConnection(ClientConnection):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.timeline=deque(maxlen=64);self.transport_counts=Counter()
        self.frame_queue_peak=0;self.pause_started=None;self.paused_ms=0;self.last_bytes_at=None
    def record_transport(self,event,**fields):
        self.transport_counts[event]+=1
        self.timeline.append({'at':int(time.time()*1000),'event':event,**fields})
    def connection_made(self,transport):
        super().connection_made(transport)
        pause=self.recv_messages.pause;resume=self.recv_messages.resume
        def paused():
            self.pause_started=time.monotonic()
            self.record_transport('read_paused',queued_frames=len(self.recv_messages.frames))
            pause()
        def resumed():
            elapsed=(time.monotonic()-self.pause_started)*1000 if self.pause_started is not None else 0
            self.paused_ms+=elapsed;self.pause_started=None
            self.record_transport('read_resumed',pause_ms=round(elapsed,2),queued_frames=len(self.recv_messages.frames))
            resume()
        self.recv_messages.pause=paused;self.recv_messages.resume=resumed
        self.record_transport('connection_made',high=self.max_queue_high,low=self.max_queue_low)
    def data_received(self,data):
        self.last_bytes_at=int(time.time()*1000)
        super().data_received(data)
    def process_event(self,event):
        if isinstance(event,Frame) and event.opcode in (Opcode.PING,Opcode.PONG,Opcode.CLOSE):
            self.record_transport('received_'+event.opcode.name.lower())
        super().process_event(event)
        if hasattr(self,'recv_messages'):
            self.frame_queue_peak=max(self.frame_queue_peak,len(self.recv_messages.frames))
    async def ping(self,data=None):
        waiter=await super().ping(data)
        self.record_transport('ping_sent',pending_pings=len(self.pending_pings))
        return waiter
    def acknowledge_pings(self,data):
        pending=self.pending_pings.get(data)
        super().acknowledge_pings(data)
        if pending:self.record_transport('pong_acknowledged',latency_ms=round((self.loop.time()-pending[1])*1000,2),pending_pings=len(self.pending_pings))
    def diagnostic_snapshot(self):
        assembler=getattr(self,'recv_messages',None)
        now=self.loop.time()
        paused=self.paused_ms+((time.monotonic()-self.pause_started)*1000 if self.pause_started is not None else 0)
        return {'timeline':list(self.timeline),'counts':dict(self.transport_counts),
            'last_bytes_at':self.last_bytes_at,'writer_put_started_at':getattr(self,'writer_put_started_at',None),
            'writer_put_age_ms':max(0,int(time.time()*1000)-self.writer_put_started_at) if getattr(self,'writer_put_started_at',None) else None,'frame_queue':len(assembler.frames) if assembler else 0,
            'frame_queue_peak':self.frame_queue_peak,'frame_queue_high':self.max_queue_high,'frame_queue_low':self.max_queue_low,
            'read_paused':getattr(assembler,'paused',False),'read_paused_ms':round(paused,2),
            'pending_pings':len(self.pending_pings),'oldest_ping_age_ms':round(max([0]+[(now-stamp)*1000 for _,stamp in self.pending_pings.values()]),2),
            'ping_interval_seconds':self.ping_interval,'ping_timeout_seconds':self.ping_timeout,
            'scope':'connection-local; 64 transitions; native transport behavior unchanged'}
    def connection_lost(self, exc):
        self.record_transport('connection_lost',exception=type(exc).__name__ if exc else None)
        # Windows TLS cancellation may call connection_lost before
        # connection_made. websockets 16.1.1 then accesses a missing assembler
        # and never resolves its close waiter. Preserve the original exception
        # and normal cleanup; do not suppress connection failures.
        if not hasattr(self,'recv_messages'):
            logging.getLogger('erji.streams').warning('websocket closed before connection_made: %s',type(exc).__name__)
            self.recv_messages=Assembler(self.max_queue_high,self.max_queue_low)
        super().connection_lost(exc)

class ReceiveTimings:
    def __init__(self):
        self.last=None;self.samples={k:deque(maxlen=300) for k in ('receive_gap_ms','event_age_ms','ingest_ms','writer_wait_ms')}
        self.packets=0;self.gaps_over_15s=0;self.stable_since=None
    def received(self,packet,at):
        if 'result' in packet or 'code' in packet:return
        if self.last is not None:
            gap=max(0,at-self.last);self.samples['receive_gap_ms'].append(gap)
            self.gaps_over_15s+=int(gap>15000)
            if gap>15000:self.stable_since=at
        if self.stable_since is None:self.stable_since=at
        self.last=at;self.packets+=1
        rows=packet.get('data',packet);rows=rows if isinstance(rows,list) else [rows]
        for r in rows:
            if isinstance(r,dict) and (r.get('E') or r.get('T')):
                self.samples['event_age_ms'].append(at-int(r.get('E') or r['T']))
    def record(self,key,started):self.samples[key].append((time.perf_counter()-started)*1000)
    def stable(self,at):return self.packets>=10 and self.stable_since is not None and at-self.stable_since>=60000 and 0<=at-self.last<=15000
    def metrics(self):
        result={'packets':self.packets,'receive_gaps_over_15s':self.gaps_over_15s,'last_receive_at':self.last,
            'scope':'connection_packets; per-market continuity measured by detector'}
        for k,rows in self.samples.items():
            values=sorted(rows)
            result[k]={'samples':len(values),'p95':round(values[min(len(values)-1,int(len(values)*.95))],2) if values else None,'max':round(max(values),2) if values else None}
        return result


async def measured_writer_put(connection,writer,item,timings):
    started=time.perf_counter();at=int(time.time()*1000)
    connection.writer_put_started_at=at
    try:await writer.put(item)
    finally:
        elapsed=(time.perf_counter()-started)*1000
        timings.record('writer_wait_ms',started)
        record=getattr(connection,'record_transport',None)
        if record and elapsed>=1000:record('writer_put_wait',started_at=at,elapsed_ms=round(elapsed,2))
        connection.writer_put_started_at=None
