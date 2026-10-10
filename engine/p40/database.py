"""Process-local all-checkout metrics, separate from over-limit warnings."""
import time
import os
import threading
import re
import logging
import json
from uuid import uuid4
from hashlib import sha256
from collections import Counter,deque
from sqlalchemy import event
from contextlib import contextmanager
from contextvars import ContextVar

_lock=threading.Lock();_data={}
_phases={}
_stage=ContextVar('p40_stage',default=())
_stage_starts=ContextVar('p40_stage_starts',default=())

def _span(name,started):
    elapsed=(time.monotonic()-started)*1000;thread=threading.get_ident()
    with _lock:
        for bucket in _data.values():
            for hold in bucket['holds'].values():
                if (name,started) in hold['_stage_context'] or (hold['thread_id']==thread and not hold['pool'].startswith('fence')):
                    hold['timeline'].append({'at':int(time.time()*1000),'stage':name[:240],'elapsed_ms':round((time.monotonic()-max(started,hold['started']))*1000,2),'scope':'stage overlap with possession'})

def record_span(name,started):
    _span(name,started)

@contextmanager
def measure(phase,purpose):
    started=time.monotonic()
    token=_stage.set((*_stage.get(),phase+':'+purpose))
    stage_token=_stage_starts.set((*_stage_starts.get(),(phase+':'+purpose,started)))
    try:yield
    finally:
        _span(phase+':'+purpose,started)
        _stage.reset(token);_stage_starts.reset(stage_token)
        with _lock:
            rows=_phases.setdefault((phase,purpose),deque(maxlen=300))
            rows.append((time.monotonic()-started)*1000)

def install(engine,scope):
    bucket={'counts':Counter(),'active':0,'peak':0,'held':deque(maxlen=1000),
        'over_limit':deque(maxlen=1000),'errors':Counter(),'last_error_at':None,'connect_ms':deque(maxlen=300),
        'sql':{k:deque(maxlen=300) for k in ('advisory_lock_ms','row_lock_ms','other_ms')}}
    bucket['slow_statements']={};bucket['holds']={};bucket['long_holds']=deque(maxlen=16)
    with _lock:_data[scope]=bucket
    @event.listens_for(engine,'do_connect')
    def attempting(dialect,record,args,kwargs):
        record.info['connect_started']=time.monotonic()
        with _lock:bucket['counts']['connect_attempts']+=1
    @event.listens_for(engine,'connect')
    def connected(dbapi,record):
        started=record.info.pop('connect_started',time.monotonic())
        with _lock:
            bucket['counts']['connections_created']+=1;bucket['connect_ms'].append((time.monotonic()-started)*1000)
    @event.listens_for(engine,'close')
    def closed(dbapi,record):
        with _lock:bucket['counts']['connections_closed']+=1
    @event.listens_for(engine,'checkout')
    def checkout(dbapi,record,proxy):
        record.info['p40_checkout']=time.monotonic()
        hold={'pool':scope,'_stage_context':_stage_starts.get(),'checkout_id':uuid4().hex,'started':record.info['p40_checkout'],
            'borrowed_at':int(time.time()*1000),'thread_id':threading.get_ident(),
            'backend_pid':getattr(getattr(dbapi,'info',None),'backend_pid',None),
            'checkout_stage':'/'.join(_stage.get()) or 'unattributed','timeline':deque(maxlen=64),
            'sql_count':0,'sql_ms':0,'transaction_started':None,'transaction_ms':0}
        record.info['p40_hold']=hold
        with _lock:
            bucket['counts']['checkouts']+=1;bucket['active']+=1;bucket['peak']=max(bucket['peak'],bucket['active']);bucket['holds'][hold['checkout_id']]=hold
    @event.listens_for(engine,'checkin')
    def checkin(dbapi,record):
        start=record.info.pop('p40_checkout',None)
        if start is None:return
        held=(time.monotonic()-start)*1000
        with _lock:
            bucket['counts']['checkins']+=1;bucket['active']-=1;bucket['held'].append(held)
            hold=record.info.pop('p40_hold',None)
            if hold:
                bucket['holds'].pop(hold['checkout_id'],None)
                for name,phase_start in _stage_starts.get():hold['timeline'].append({
                    'at':int(time.time()*1000),'stage':name[:240],
                    'elapsed_ms':round((time.monotonic()-max(phase_start,start))*1000,2),
                    'scope':'stage overlap ending at connection return'})
            if held>10000:
                bucket['counts']['held_over_10s']+=1;bucket['over_limit'].append(held)
                evidence={k:v for k,v in (hold or {}).items() if k not in ('started','timeline','transaction_started','_stage_context')}
                evidence.update(run_id=os.getenv('ERJI_RUN_ID'),pid=os.getpid(),pool=scope,
                    returned_at=int(time.time()*1000),hold_ms=round(held,2),
                    timeline=list((hold or {}).get('timeline',())),
                    scope='client connection possession; transaction spans are client-side events')
                bucket['long_holds'].append(evidence)
        if held>10000:logging.getLogger('erji.db').warning('database_connection_long_hold %s',json.dumps(evidence))
    @event.listens_for(engine,'begin')
    def transaction_begin(conn):
        with _lock:
            hold=conn.info.get('p40_hold')
            if hold:
                hold['transaction_started']=time.monotonic()
                hold['timeline'].append({'at':int(time.time()*1000),'stage':'transaction_begin'})
    def transaction_end(conn,label):
        if conn.closed or conn.invalidated:return
        with _lock:
            hold=conn.info.get('p40_hold')
            if hold:
                start=hold.pop('transaction_started',None)
                elapsed=(time.monotonic()-start)*1000 if start is not None else 0
                hold['transaction_ms']+=elapsed
                hold['timeline'].append({'at':int(time.time()*1000),'stage':label,'elapsed_ms':round(elapsed,2)})
    event.listen(engine,'commit',lambda conn:transaction_end(conn,'commit_requested'))
    event.listen(engine,'rollback',lambda conn:transaction_end(conn,'rollback_requested'))
    @event.listens_for(engine,'handle_error')
    def failed(context):
        error=context.original_exception;winerror='10055' in str(error)
        code='winerror_10055' if winerror else getattr(error,'sqlstate',None) or type(error).__name__
        with _lock:bucket['errors'][code]+=1;bucket['last_error_at']=int(time.time()*1000)
    @event.listens_for(engine,'before_cursor_execute')
    def sql_start(conn,cursor,statement,parameters,context,many):
        context.p40_started=time.monotonic()
    @event.listens_for(engine,'after_cursor_execute')
    def sql_end(conn,cursor,statement,parameters,context,many):
        upper=statement.upper()
        kind='advisory_lock_ms' if 'PG_ADVISORY' in upper or 'PG_TRY_ADVISORY' in upper else 'row_lock_ms' if 'FOR UPDATE' in upper else 'other_ms'
        elapsed=(time.monotonic()-context.p40_started)*1000
        fingerprint=None
        if elapsed>=1000 or kind!='other_ms':
            shape=re.sub(r"'(?:''|[^'])*'|\b\d+\b",'?',upper)
            fingerprint=sha256(shape.encode()).hexdigest()[:16]
        with _lock:
            hold=conn.info.get('p40_hold')
            if hold:
                hold['sql_count']+=1;hold['sql_ms']+=elapsed
                if fingerprint:hold['timeline'].append({'at':int(time.time()*1000),
                    'stage':'/'.join(_stage.get()) or 'unattributed','sql_kind':kind,'fingerprint':fingerprint,'elapsed_ms':round(elapsed,2)})
            bucket['sql'][kind].append(elapsed)
            if elapsed>=1000:
                statements=bucket['slow_statements']
                if fingerprint not in statements and len(statements)>=16:statements.pop(next(iter(statements)))
                previous=statements.get(fingerprint,{'count':0,'max_ms':0})
                statements[fingerprint]={'count':previous['count']+1,'max_ms':round(max(previous['max_ms'],elapsed),2),'kind':kind,'stage':'/'.join(_stage.get())[:240] or 'unattributed','last_at':int(time.time()*1000)}

def snapshot():
    def distribution(rows):
        values=sorted(rows)
        return {'samples':len(values),'p95_ms':round(values[min(len(values)-1,int(len(values)*.95))],2) if values else None}
    with _lock:
        return {'run_id':os.getenv('ERJI_RUN_ID'),'pid':os.getpid(),'scope':'process-local counters since start; bounded distributions',
            'phases':{phase+':'+purpose:distribution(rows) for (phase,purpose),rows in _phases.items()},
            'pools':{name:{'counts':dict(b['counts']),'active':b['active'],'peak':b['peak'],
                'hold_ms':distribution(b['held']),'checkout_ms':distribution(b['held']),
                'checkout_ms_semantics':'deprecated alias of hold_ms; not pre-checkout waiting',
                'long_holds':list(b['long_holds']),
                'active_hold_examples':[{k:v for k,v in h.items() if k not in ('started','timeline','transaction_started','_stage_context')} |
                    {'hold_ms':round((time.monotonic()-h['started'])*1000,2),'timeline':list(h['timeline'])} for h in list(b['holds'].values())[:16]],'over_10s_ms':distribution(b['over_limit']),
                'connect_ms':distribution(b['connect_ms']),'statement_timings':{k:distribution(v) for k,v in b['sql'].items()},
                'slow_statements':dict(b['slow_statements']),
                'errors':dict(b['errors']),'last_error_at':b['last_error_at']}
                for name,b in _data.items()}}
