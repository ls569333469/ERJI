from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session as SQLSession
import threading,time,logging
from collections import deque
from sqlalchemy import event
from engine.config import settings
from contextvars import ContextVar
owner_context=ContextVar('worker_owner',default=None)

class Base(DeclarativeBase):
    pass

engine = create_engine(settings().database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)
from engine.p40.database import install as install_metrics,measure,record_span
install_metrics(engine,'query')
# Admission happens before opening a transaction. Nested helpers in one thread
# reuse an admission slot instead of deadlocking behind their own outer session.
_admission=threading.BoundedSemaphore(8)
_local=threading.local()
_metric_lock=threading.Lock();_samples={k:deque(maxlen=300) for k in ('admission_ms','session_ms','commit_ms')}
def record_metric(key,value):
    with _metric_lock:_samples.setdefault(key,deque(maxlen=300)).append(round(value,2))
def metrics():
    with _metric_lock:
        result={k:{'samples':len(v),'p95':sorted(v)[min(len(v)-1,int(len(v)*.95))] if v else None} for k,v in _samples.items()}
    from engine.p40.database import snapshot
    return {**result,'connections':snapshot()}
class BoundedSession(SQLSession):
    def __enter__(self):
        self.info['role_owner']=owner_context.get() or role_owner
        depth=getattr(_local,'depth',0)
        started=time.monotonic()
        if depth==0:_admission.acquire()
        self.info['admission_ms']=round((time.monotonic()-started)*1000,2)
        record_metric('admission_ms',self.info['admission_ms']);self.info['session_started']=time.monotonic()
        _local.depth=depth+1
        return super().__enter__()
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:
            record_metric('session_ms',(time.monotonic()-self.info.pop('session_started',time.monotonic()))*1000)
            _local.depth-=1
            if _local.depth==0:_admission.release()

Session = sessionmaker(bind=engine,class_=BoundedSession,expire_on_commit=False)

role_owner=None

@event.listens_for(SQLSession,'before_commit')
def _fence_commit(session):
    session.info['commit_started']=time.monotonic()
    from engine.p38.lifecycle import commit_guard
    owner=session.info.get('role_owner',owner_context.get() or role_owner)
    if owner is not None:
        started=time.monotonic()
        try:
            with measure('commit_guard','role'):owner.guard(session)
        finally:record_metric('role_fence_ms',(time.monotonic()-started)*1000)
    guard=commit_guard.get()
    if guard is not None:
        started=time.monotonic()
        try:
            with measure('commit_guard','lease_source'):guard(session)
        finally:record_metric('lease_source_guard_ms',(time.monotonic()-started)*1000)
    from engine.transaction_events import publish
    started=time.monotonic()
    try:
        with measure('commit_tail','publication'):publish(session)
    finally:record_metric('publication_tail_ms',(time.monotonic()-started)*1000)

@event.listens_for(SQLSession,'after_rollback')
def _discard_events(session):
    from engine.transaction_events import discard
    discard(session)
    session.info.pop('p39_locked_demands',None)
    session.info.pop('p39_demand_rows',None)

@event.listens_for(SQLSession,'after_transaction_end')
def _clear_demand_reservations(session,transaction):
    if transaction.parent is None:
        session.info.pop('p39_locked_demands',None);session.info.pop('p39_demand_rows',None)

@event.listens_for(BoundedSession,'after_commit')
def _commit_metric(session):
    started=session.info.pop('commit_started',None)
    if started is not None:
        record_metric('commit_ms',(time.monotonic()-started)*1000)
        record_span('session_commit_total_including_guards',started)

@event.listens_for(engine,'checkout')
def _checkout(dbapi_connection,record,proxy):
    record.info['checked_out_at']=time.monotonic()

@event.listens_for(engine,'checkin')
def _checkin(dbapi_connection,record):
    started=record.info.pop('checked_out_at',None)
    if started and time.monotonic()-started>10:
        logging.getLogger('erji.db').warning('database connection held %.1fs',time.monotonic()-started)


def session_scope():
    with Session() as session:
        yield session

