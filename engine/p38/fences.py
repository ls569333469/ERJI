"""Dedicated bounded session fences; never share ordinary query connections."""
from sqlalchemy import create_engine,event
from engine.db import engine
import threading

def make_engine(url,size=8,scope='fence'):
    value=create_engine(url,pool_pre_ping=True,pool_size=size,max_overflow=0,pool_timeout=30,
        pool_reset_on_return=None,isolation_level='AUTOCOMMIT')
    @event.listens_for(value,'reset')
    def release_all(dbapi,record,state):
        if state.terminate_only:return
        # Explicit unlock in task cleanup is not enough: exceptions, cancellation
        # and reentrant session locks must never leak into the next borrower.
        dbapi.rollback()
        previous=dbapi.autocommit
        try:
            dbapi.autocommit=True
            with dbapi.cursor() as cursor:cursor.execute('SELECT pg_advisory_unlock_all()')
        finally:dbapi.autocommit=previous
        # SQLAlchemy invalidates the connection when reset raises.
    from engine.p40.database import install
    install(value,scope)
    return value

# Per process: preserve the total of eight sessions. Collector threads cannot
# consume the two entry/protect reservations, even on a separate asyncio loop.
fence_engine=make_engine(engine.url,4,'fence_analysis')
urgent_engine=make_engine(engine.url,2,'fence_urgent')
gap_engine=make_engine(engine.url,2,'fence_gap')
_budgets={name:threading.BoundedSemaphore(size) for name,size in (('analysis',4),('urgent',2),('gap',2))}

def connect():return fence_engine.connect().execution_options(isolation_level='AUTOCOMMIT')

def connect_for(kind):
    from engine.p40.database import measure
    with measure('fence_acquire',kind):
        if kind in ('entry','protect'):return urgent_engine.connect().execution_options(isolation_level='AUTOCOMMIT')
        if kind=='gap':return gap_engine.connect().execution_options(isolation_level='AUTOCOMMIT')
        return connect()

class BorrowedFence:
    def __init__(self,connection,budget):
        self.connection=connection;self.budget=budget;self.closed=False;self.lock=threading.Lock()
    def __getattr__(self,name):return getattr(self.connection,name)
    def close(self):
        with self.lock:
            if self.closed:return
            self.closed=True
            try:self.connection.close()
            finally:self.budget.release()

async def borrow(kind):
    """Cross-loop admission before checkout; wait without occupying executor threads."""
    import asyncio
    from engine.p38.lifecycle import acquire_thread
    from engine.p40.database import measure
    purpose='urgent' if kind in ('entry','protect') else 'gap' if kind=='gap' else 'analysis'
    budget=_budgets[purpose]
    with measure('fence_admission',purpose):
        while not budget.acquire(blocking=False):await asyncio.sleep(.1)
    try:
        connection=await acquire_thread(lambda:connect_for(kind),lambda c:c.close())
        return BorrowedFence(connection,budget)
    except BaseException:
        budget.release();raise
