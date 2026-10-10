"""Retry known rolled-back database transactions, never unknown commits/I/O."""
import asyncio,random,time,logging
from functools import wraps
from contextvars import ContextVar
_retrying=ContextVar('database_retry_scope',default=False)

def sqlstate(error):
    return getattr(getattr(error,'orig',error),'sqlstate',None)

def retryable(error):return sqlstate(error) in ('40P01','40001')

def retry_transaction(fn):
    """fn must open its own Session and perform only replay-safe DB work."""
    @wraps(fn)
    def run(*args,**kwargs):
        if _retrying.get():return fn(*args,**kwargs)
        token=_retrying.set(True)
        try:
            for attempt in range(4):
                try:return fn(*args,**kwargs)
                except Exception as error:
                    if not retryable(error) or attempt==3:raise
                    logging.getLogger('erji.db').warning('%s transaction retry %s sqlstate=%s',fn.__name__,attempt+1,sqlstate(error))
                    time.sleep(.025*2**attempt+random.uniform(0,.025))
        finally:_retrying.reset(token)
    return run

async def retry_write(fn,on_retry=None):
    token=_retrying.set(True)
    try:
        for attempt in range(4):
            try:return await fn()
            except Exception as error:
                if not retryable(error) or attempt==3:raise
                if on_retry:on_retry()
                await asyncio.sleep(.025*2**attempt+random.uniform(0,.025))
    finally:_retrying.reset(token)
