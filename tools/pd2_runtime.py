"""Isolated development/test runtime. Never rewrites the source schema."""
import os,sys,json,argparse,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.dev-deps'))
from engine.config import Settings
from sqlalchemy import create_engine,text,inspect
from sqlalchemy.engine import make_url
source=Settings(_env_file="D:/Axiangmu/erji/.env")
SCHEMA="erji_pd2_20260924"
url=make_url(source.database_url).update_query_dict({"options":"-csearch_path="+SCHEMA})
os.environ["ERJI_DATABASE_URL"]=url.render_as_string(hide_password=False)
os.environ["ERJI_CMC_KEY_FILE"]="F:/key/CMC.txt"
os.environ["ERJI_BYKARANTELI_KEY_FILE"]="F:/key/bykaranteli.txt"
os.environ["ERJI_WEB_ORIGIN"]="http://127.0.0.1:18800"
os.environ["ERJI_API_ORIGIN"]="http://127.0.0.1:18801"
parser=argparse.ArgumentParser()
parser.add_argument("role",choices=["init","test","api","web","live","jobs","notify","once","contracts","streams"])
args,extra=parser.parse_known_args()

if args.role=="init":
    admin=create_engine(source.database_url,isolation_level="REPEATABLE READ")
    with admin.begin() as c:
        if c.execute(text("SELECT 1 FROM information_schema.schemata WHERE schema_name=:s"),{"s":SCHEMA}).scalar():
            raise SystemExit("Isolated schema already exists; no copy performed.")
        c.execute(text("CREATE SCHEMA "+SCHEMA))
        source_tables=inspect(c).get_table_names(schema="erji_market_expansion_verify")
        for name in source_tables:
            if name=="alembic_version":continue
            c.execute(text(f'CREATE TABLE "{SCHEMA}"."{name}" (LIKE "erji_market_expansion_verify"."{name}" INCLUDING ALL)'))
            c.execute(text(f'INSERT INTO "{SCHEMA}"."{name}" SELECT * FROM "erji_market_expansion_verify"."{name}"'))
            # LIKE includes source nextval defaults; replace with an independent sequence.
            for col in inspect(c).get_columns(name,schema=SCHEMA):
                if "nextval(" in str(col.get("default","")):
                    seq=f'{SCHEMA}.{name}_{col["name"]}_seq'
                    c.execute(text(f'CREATE SEQUENCE {seq}'))
                    c.execute(text(f'ALTER TABLE "{SCHEMA}"."{name}" ALTER COLUMN "{col["name"]}" SET DEFAULT nextval(\'{seq}\')'))
                    c.execute(text(f"SELECT setval('{seq}', GREATEST(COALESCE((SELECT MAX(\"{col['name']}\") FROM \"{SCHEMA}\".\"{name}\"),0),1), true)"))
    from engine.db import Base,engine as db_engine
    import engine.models
    Base.metadata.create_all(db_engine)
    from alembic.config import Config
    from alembic import command
    command.stamp(Config(str(ROOT/"alembic.ini")),"head")
    print(json.dumps({"schema":SCHEMA,"source":"erji_market_expansion_verify","copied_tables":len(source_tables),"source_unchanged":True}))
elif args.role=="test":
    import pytest
    raise SystemExit(pytest.main(extra or ["tests","-q"]))
elif args.role=="contracts":
    from tools.export_contracts import *
elif args.role=="api":
    import uvicorn
    uvicorn.run("services.api.main:app",host="127.0.0.1",port=18801)
elif args.role=="web":
    raise SystemExit(subprocess.call(["C:/Program Files/nodejs/node.exe",str(ROOT/"node_modules/next/dist/bin/next"),"dev",str(ROOT/"apps/web"),"--webpack","--hostname","127.0.0.1","--port","18800"],env=os.environ))
else:
    import asyncio
    from services.worker.main import run
    asyncio.run(run("jobs" if args.role=="once" else args.role,once=args.role=="once"))
