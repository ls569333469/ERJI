"""Run via p25_verify.py: real collection into the isolated local schema only."""
import asyncio
import json

from engine.config import settings
from engine.db import Session
from engine.models import WorkerHeartbeat
from services.worker.main import run
from sqlalchemy.engine import make_url

url = make_url(settings().database_url)
if url.host not in ('127.0.0.1', 'localhost') or 'erji_p25_20260929' not in url.query.get('options', ''):
    raise SystemExit('Local isolated schema required')

asyncio.run(run('binance-bridge', once=True))
with Session() as session:
    row = session.get(WorkerHeartbeat, 'bridge_binance')
    print(json.dumps({'state': row.state, 'updated_at': row.updated_at, **row.details}))
