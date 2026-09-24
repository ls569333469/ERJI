import sys, json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine.config import Settings
from sqlalchemy import create_engine,text
cfg=Settings(_env_file="D:/Axiangmu/erji/.env")
e=create_engine(cfg.database_url,connect_args={"connect_timeout":5})
with e.connect() as c:
    schemas=[r[0] for r in c.execute(text("SELECT schema_name FROM information_schema.schemata WHERE schema_name IN ('public','erji_market_expansion_verify')"))]
    out={}
    for schema in schemas:
        out[schema]={}
        for table in ("candles","episodes","plans","signal_snapshots","oi_points"):
            out[schema][table]=c.execute(text(f'SELECT count(*) FROM "{schema}"."{table}"')).scalar()
    print(json.dumps(out))
