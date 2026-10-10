"""One finite post-change replay from the saved public fixture; no live reads."""
from pathlib import Path
import os,sys,socket,subprocess,time,json,shutil

root=Path(__file__).resolve().parents[1]
out=root/'docs/p40-validation/implementation-20261009'
out.mkdir(parents=True,exist_ok=True)
old=root/'docs/p39-validation/capacity-replay-20261008'
assert (old/'public-inputs.pickle').exists(), 'Saved public fixture is required'
pg=Path('D:/PostgreSQL/bin')
cluster=root/'.test-tmp'/('priority-capacity-'+str(time.time_ns()))
cluster.mkdir(parents=True)
with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
flags=subprocess.CREATE_NO_WINDOW

def run_pg(name,*args):
    with (out/(name+'.log')).open('ab') as log:
        result=subprocess.run([str(pg/(name+'.exe')),*map(str,args)],stdout=log,stderr=log,
            stdin=subprocess.DEVNULL,creationflags=flags,timeout=60)
    if result.returncode:raise RuntimeError(name+' failed')

def child(*args):
    env=os.environ.copy();env['ERJI_BUDGET_OWNER']='0';env['PGCONNECT_TIMEOUT']='5'
    result=subprocess.run([sys.executable,'-X','utf8',str(root/'tools/p39_capacity_replay.py'),*args],
        env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,encoding='utf8',errors='replace',creationflags=flags,timeout=240)
    (out/(args[0]+'.log')).write_text(result.stdout,'utf8')
    if result.returncode:raise RuntimeError(result.stdout[-3500:])

try:
    run_pg('initdb','-D',cluster,'-U','capacity','--encoding=UTF8','--locale=C','--auth=trust')
    run_pg('pg_ctl','-D',cluster,'-l',cluster/'server.log','-o',f'-h 127.0.0.1 -p {port} -c shared_buffers=128MB','-w','start')
    import psycopg
    with psycopg.connect(host='127.0.0.1',port=port,user='capacity',dbname='postgres',autocommit=True) as c:c.execute('CREATE DATABASE p40_priority_capacity_check')
    url=f'postgresql+psycopg://capacity@127.0.0.1:{port}/p40_priority_capacity_check'
    child('seed',url)
    label='p40-updated-code-20261009-c2'
    child('run',url,'2',label)
    shutil.copy2(old/(label+'.json'),out/'real-executor-replay.json')
    result=json.loads((out/'real-executor-replay.json').read_text('utf8'))
    assert result['completed_tasks']==24 and result['deadlocks']==0
    assert result['observed_peak_inflight']=={'structure':2,'trend':2,'total':4}
    print(json.dumps({k:result[k] for k in ('completed_tasks','elapsed_seconds','deadlocks','observed_peak_inflight')},ensure_ascii=False))
finally:
    if (cluster/'postmaster.pid').exists():run_pg('pg_ctl','-D',cluster,'-m','fast','-w','stop')
    (out/'replay-isolation.json').write_text(json.dumps({'cluster':str(cluster),'port':port,
        'production_reads':False,'production_writes':False,'exchange_calls':False,
        'stopped':not (cluster/'postmaster.pid').exists(),'fixture':'saved 12-market public candle fixture'},indent=2),'utf8')
