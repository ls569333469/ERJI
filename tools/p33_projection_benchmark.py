"""300-row projection benchmark in a disposable local database schema.

Creates no opportunities in the live schema and performs no provider requests.
The measurement excludes Next.js/network overhead and is labelled accordingly.
"""
from pathlib import Path
import sys,time,json,uuid,concurrent.futures
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[4]/'.env');load_dotenv(Path(__file__).resolve().parents[1]/'.env',override=True)
from sqlalchemy import create_engine,text
from sqlalchemy.orm import sessionmaker
from engine.db import engine,Base
from engine.pd2 import signal_projection as projection
from test_p25_signals import seed,NOW


def run():
    if engine.url.host not in ('localhost','127.0.0.1'):raise RuntimeError('local_database_required')
    schema='p33_bench_'+uuid.uuid4().hex
    test_engine=None;old_session=projection.Session;old_clock=projection.now_ms
    try:
        with engine.begin() as c:c.execute(text('CREATE SCHEMA '+schema))
        test_engine=create_engine(engine.url,connect_args={'options':'-csearch_path='+schema},pool_size=4,max_overflow=0)
        Base.metadata.create_all(test_engine)
        factory=sessionmaker(bind=test_engine,expire_on_commit=False);seed(factory,300,action='waiting')
        projection.Session=factory;projection.now_ms=lambda:NOW
        def get(_):
            started=time.perf_counter();data=projection.read('opportunities',size=300)
            encoded=json.dumps(data,ensure_ascii=False).encode()
            return {'ms':round((time.perf_counter()-started)*1000,1),'rows':len(data['rows']),'bytes':len(encoded)}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(get,range(20)))
        times=sorted(r['ms'] for r in rows)
        result={'kind':'controlled_local_postgres_projection_not_end_to_end_http','external_requests':0,
            'live_schema_written':False,'concurrency':4,'samples':rows,'p95_ms':times[18],
            'max_ms':max(times),'all_300_rows':all(r['rows']==300 for r in rows),'schema_removed':False}
    finally:
        projection.Session=old_session;projection.now_ms=old_clock
        if test_engine:test_engine.dispose()
        with engine.begin() as c:c.execute(text('DROP SCHEMA IF EXISTS '+schema+' CASCADE'))
    result['schema_removed']=True
    return result


if __name__=='__main__':
    result=run();out=Path('docs/p33-validation/remaining-20261002/projection-load.json')
    out.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='samples'}))
    if not result['all_300_rows'] or result['p95_ms']>2000:raise SystemExit(1)
