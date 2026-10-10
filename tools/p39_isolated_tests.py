"""P39 tests in a disposable DB; fixtures may only read bounded public BTC data."""
from pathlib import Path
import sys,os,subprocess,time,json,socket
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));os.chdir(root)
pg=Path('D:/PostgreSQL/bin');data=root/'.test-tmp'/('p39-pg-'+str(time.time_ns()));data.mkdir(parents=True)
flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
def run(name,*args):
    output=data.parent/(data.name+'-'+name+'-command.log')
    with output.open('ab') as log:result=subprocess.run([str(pg/(name+'.exe')),*map(str,args)],stdout=log,stderr=log,stdin=subprocess.DEVNULL,creationflags=flags,timeout=60)
    if result.returncode:raise RuntimeError(output.read_text(encoding='utf8',errors='replace')[-3000:])
try:
    run('initdb','-D',data,'-U','p39test','--encoding=UTF8','--locale=C','--auth=trust')
    run('pg_ctl','-D',data,'-l',data/'server.log','-o',f'-h 127.0.0.1 -p {port}','-w','start')
    import psycopg
    name='erji_p36_check_p39_'+str(int(time.time()))
    with psycopg.connect(host='127.0.0.1',port=port,user='p39test',dbname='postgres',autocommit=True) as c:c.execute('CREATE DATABASE "'+name+'"')
    os.environ['ERJI_DATABASE_URL']=f'postgresql+psycopg://p39test@127.0.0.1:{port}/{name}'
    os.environ['ERJI_REQUIRE_AUTH']='false';os.environ['ERJI_BUDGET_OWNER']='0'
    from engine.db import engine,Base
    from engine import models
    from engine.repository import bootstrap
    Base.metadata.create_all(engine);bootstrap()
    # Synthetic fixtures live only in this disposable cluster. No exchange I/O.
    from engine.adapters.base import Batch,now_ms
    from engine.repository import save_batch
    at=now_ms();iid='binance:perp:BTCUSDT';batch=Batch('binance')
    batch.instruments=[dict(id=iid,asset_id='BTC',venue='binance',native_symbol='BTCUSDT',market_type='linear_perpetual',quote_asset='USDT',spec={'base_asset':'BTC','status':'TRADING','tick_size':'0.1'})]
    batch.states=[dict(instrument_id=iid,as_of=at,received_at=at,price='100',price_kind='last',raw_ref='p39-fixture')]
    batch.raw=[dict(id='p39-fixture',source='binance',endpoint='test:synthetic',received_at=at,payload={'test_fixture':True})]
    for tf,step in [('60',3600000),('240',14400000),('1D',86400000),('1W',604800000)]:
        offset=345600000 if tf=='1W' else 0;end=(at-offset)//step*step+offset
        for n in range(300):
            start=end-(300-n)*step
            batch.candles.append(dict(instrument_id=iid,resolution=tf,open_time=start,close_time=start+step,is_closed=True,open='100',high='101',low='99',close='100',base_volume='10',quote_volume='1000',received_at=at,raw_ref='p39-fixture'))
    save_batch(batch)
    import pytest
    prefix=os.environ.get('ERJI_TEST_REPORT_PREFIX','')
    if prefix and not all(c.isalnum() or c=='-' for c in prefix):raise ValueError('invalid report prefix')
    prefix=prefix+'-' if prefix else ''
    result=pytest.main((sys.argv[1:] or ['tests'])+['-q','--basetemp=./.test-tmp/p39-pytest','--junitxml=docs/p39-validation/'+prefix+'tests.xml'])
    engine.dispose()
    with psycopg.connect(host='127.0.0.1',port=port,user='p39test',dbname='postgres',autocommit=True) as c:c.execute('DROP DATABASE "'+name+'" WITH (FORCE)')
    Path('docs/p39-validation/'+prefix+'test-isolation.json').write_text(json.dumps({'database':name,'port':port,'production_access':False,'fixture':'test-owned PostgreSQL cluster; no production copies','exit_code':int(result)},indent=2),encoding='utf8')
finally:
    if (data/'postmaster.pid').exists():run('pg_ctl','-D',data,'-m','fast','-w','stop')
raise SystemExit(result)
