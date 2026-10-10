"""Real HTTP on the production API, with 300 isolated rows copied from real fields.

Cloned identities and frozen quote times are load fixtures, not new opportunities.
The live schema is read only; no provider calls or strategy releases occur.
"""
from tools.p34_local import prepare
prepare()
import concurrent.futures,json,time,threading,socket,uuid,os,subprocess,sys
from copy import deepcopy
from pathlib import Path
from sqlalchemy import create_engine,text,select
from sqlalchemy.orm import sessionmaker
from engine.db import engine,Base,Session
from engine.models import Asset,Instrument,MarketState,CurrentSignal
from engine.pd2 import signal_projection as projection
from services.api.main import app
import uvicorn,urllib.request

def measure(url,n=20,concurrency=4):
    def get(_):
        begin=time.perf_counter()
        with urllib.request.urlopen(url,timeout=15) as r:body=r.read()
        data=json.loads(body)
        return {'ms':round((time.perf_counter()-begin)*1000,2),'rows':len(data['rows']),'bytes':len(body)}
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:rows=list(pool.map(get,range(n)))
    values=sorted(r['ms'] for r in rows)
    return {'concurrency':concurrency,'samples':rows,'p50_ms':values[len(values)//2],
            'p95_ms':values[int(len(values)*.95)-1],'p99_ms':max(values)}

def run():
    assert engine.url.host in ('localhost','127.0.0.1')
    with Session() as s:
        sources=list(s.scalars(select(CurrentSignal).where(CurrentSignal.payload['opportunity_eligible'].as_boolean()==True).limit(30)))
    if not sources:return {'status':'blocked','reason':'no_real_candidate_payload_for_load_fixture'}
    schema='p34_load_'+uuid.uuid4().hex;db=None;server=None;thread=None;next_process=None;next_log=None
    old_session,old_clock=projection.Session,projection.now_ms;now=int(time.time()*1000)
    out={'kind':'isolated_300_row_production_api_http_real_field_clones','live_schema_written':False,
         'provider_requests':0,'source_record_ids':[r.id for r in sources],'frozen_fixture_time':now,
         'limitation':'Cloned identities/times test load, not strategy efficacy. Next proxy measured separately.'}
    try:
        with engine.begin() as c:c.execute(text('CREATE SCHEMA '+schema))
        db=create_engine(engine.url,connect_args={'options':'-csearch_path='+schema},pool_size=5,max_overflow=5)
        Base.metadata.create_all(db);factory=sessionmaker(bind=db,expire_on_commit=False)
        with factory.begin() as s:
            for n in range(300):
                original=sources[n%len(sources)];asset='P34LOAD'+str(n);iid='binance:perp:'+asset+'USDT'
                payload=deepcopy(original.payload)
                payload.update(id='p34-load-'+str(n),asset_id=asset,instrument_id=iid,symbol=asset,
                    action_code='waiting',phase='structure_pending',valid_until=now+3600000,structure_valid_until=now+3600000,
                    opportunity_eligible=True,as_of=now)
                price=payload.get('price') or payload.get('first_seen_price')
                s.add(Asset(id=asset,symbol=asset,known_at=now));s.flush()
                s.add(Instrument(id=iid,asset_id=asset,venue='binance',native_symbol=asset+'USDT',
                    market_type='linear_perpetual',quote_asset='USDT',spec={}));s.flush()
                s.add(MarketState(instrument_id=iid,as_of=now,payload={'price':price,'price_kind':'last'}))
                s.add(CurrentSignal(id='p34-load-'+str(n),asset_id=asset,instrument_id=iid,action='waiting',
                    first_seen_at=original.first_seen_at,computed_at=now,valid_until=now+3600000,payload=payload))
        projection.Session=factory;projection.now_ms=lambda:now
        sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128);port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',access_log=False,lifespan='off'))
        thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
        for _ in range(100):
            if server.started:break
            time.sleep(.05)
        base=f'http://127.0.0.1:{port}/v1/signals?view=opportunities'
        out['cold']=measure(base+'&page_size=300',1,1)
        out['full_page']=measure(base+'&page_size=300')
        out['pagination']=measure(base+'&page_size=50&page=2')
        out['filter_sort']=measure(base+'&page_size=300&q=P34LOAD&sort=oi')
        out['passed']=all(r['rows']==300 for r in out['full_page']['samples']) and all(
            out[name]['p95_ms']<=2000 for name in ('full_page','pagination','filter_sort'))
        if '--with-next' in sys.argv:
            # Separate production output and port: never replace the running
            # development build, its schema, or its API origin.
            env={**os.environ,'ERJI_NEXT_DIST_DIR':'.next-p34-validation','ERJI_API_ORIGIN':f'http://127.0.0.1:{port}'}
            next_cli=str(Path('node_modules/next/dist/bin/next').resolve())
            next_log=open('docs/p34-validation/next-build.log','w',encoding='utf-8')
            built=subprocess.run(['node',next_cli,'build','apps/web','--webpack'],env=env,stdout=next_log,stderr=next_log,timeout=240)
            if built.returncode:raise RuntimeError('isolated_next_build_failed; see next-build.log')
            free=socket.socket();free.bind(('127.0.0.1',0));webport=free.getsockname()[1];free.close()
            next_process=subprocess.Popen(['node',next_cli,'start','apps/web','-H','127.0.0.1','-p',str(webport)],
                env=env,stdout=next_log,stderr=next_log)
            web=f'http://127.0.0.1:{webport}/v1/signals?view=opportunities'
            for _ in range(80):
                try:
                    with urllib.request.urlopen(web,timeout=1) as r:r.read()
                    break
                except Exception:time.sleep(.25)
            out['next_proxy']={
                'first_screen':measure(web+'&page_size=300',1,1),
                'full_page':measure(web+'&page_size=300'),
                'pagination':measure(web+'&page_size=50&page=2'),
                'filter_sort':measure(web+'&page_size=300&q=P34LOAD&sort=oi')}
            out['next_proxy']['passed']=all(r['rows']==300 for r in out['next_proxy']['full_page']['samples']) and all(
                out['next_proxy'][name]['p95_ms']<=2000 for name in ('full_page','pagination','filter_sort'))
            out['passed']=out['passed'] and out['next_proxy']['passed']
            out['limitation']='300 cloned real field records with fixed times; API and isolated production Next proxy; not 300 natural live opportunities.'
    finally:
        if next_process:
            next_process.terminate()
            try:next_process.wait(timeout=10)
            except subprocess.TimeoutExpired:next_process.kill();next_process.wait(timeout=10)
        if next_log:next_log.close()
        if server:server.should_exit=True
        if thread:thread.join(timeout=10)
        projection.Session,projection.now_ms=old_session,old_clock
        if db:db.dispose()
        with engine.begin() as c:c.execute(text('DROP SCHEMA IF EXISTS '+schema+' CASCADE'))
    out['schema_removed']=True
    return out

if __name__=='__main__':
    result=run();dest=Path('docs/p34-validation');dest.mkdir(exist_ok=True)
    (dest/'http-load.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('source_record_ids','cold','full_page','pagination','filter_sort')},ensure_ascii=False))
    if not result.get('passed'):raise SystemExit(1)
