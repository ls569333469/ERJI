import os,sys,time,json
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from engine.config import Settings
from sqlalchemy import create_engine,text,inspect
from sqlalchemy.engine import make_url
u=make_url(Settings(_env_file='D:/Axiangmu/erji/.env').database_url);name='erji_pd2_fresh_'+str(int(time.time()))
with create_engine(u,isolation_level='AUTOCOMMIT').connect() as c:c.execute(text('CREATE DATABASE '+name))
os.environ['ERJI_DATABASE_URL']=u.set(database=name).render_as_string(hide_password=False)
from alembic.config import Config
from alembic import command
cfg=Config(str(root/'alembic.ini'));command.upgrade(cfg,'head');command.check(cfg)
from engine.db import engine
from engine.repository import bootstrap
bootstrap()
with engine.connect() as c:
 report={'database':name,'migration':'fresh_to_head','schema_drift':False,'tables':len(inspect(c).get_table_names()),'alembic':c.execute(text('SELECT version_num FROM alembic_version')).scalar(),'bootstrap':True}
(root/'docs'/'pd2-fresh-migration.json').write_text(json.dumps(report,indent=2),encoding='utf8');print(json.dumps(report))
