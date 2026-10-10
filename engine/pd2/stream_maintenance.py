"""Local DB maintenance runs independently of socket reception."""
import asyncio,logging
from engine.db_retry import sqlstate

async def maintain(fn,interval=10):
    while True:
        try:await fn()
        except asyncio.CancelledError:raise
        except Exception as error:
            logging.getLogger('erji.streams').warning('receiver maintenance failed: %s sqlstate=%s',type(error).__name__,sqlstate(error))
        await asyncio.sleep(interval)
