"""Run local verification in a dedicated schema without exposing credentials."""
import os, sys, runpy
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.config import Settings
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
url = make_url(Settings(_env_file='D:/Axiangmu/erji/.env').database_url)
if url.host not in ('localhost', '127.0.0.1'):
    raise SystemExit('Local database required')
schema = 'erji_p25_20260929'
with create_engine(url).begin() as conn:
    conn.execute(text('CREATE SCHEMA IF NOT EXISTS '+schema))
os.environ['ERJI_DATABASE_URL'] = url.update_query_dict({'options':'-csearch_path='+schema}).render_as_string(hide_password=False)
if sys.argv[1:] == ['migrate']:
    from alembic import command
    from alembic.config import Config
    command.upgrade(Config('alembic.ini'), 'head')
elif sys.argv[1:2] == ['test']:
    import pytest
    raise SystemExit(pytest.main(sys.argv[2:] or ['tests', '-q']))
else:
    runpy.run_path(sys.argv[1], run_name='__main__')
