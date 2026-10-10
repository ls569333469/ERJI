"""Finite capacity replay. Production: bounded read-only snapshot only.

Every mutation and replay executes in a separate test-owned PostgreSQL cluster.
No exchange calls, production restart, deployment, or business-code changes.
"""
from pathlib import Path
import os,sys,time,json,pickle,subprocess,socket,asyncio,threading,statistics
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
OUT=ROOT/'docs/p39-validation/capacity-replay-20261008';OUT.mkdir(exist_ok=True)
FLAGS=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0

class ResourceProbe:
    """Windows counters using the standard library; no runtime installation."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct=ctypes;self.wt=wintypes;self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.GetCurrentProcess.restype=wintypes.HANDLE
        self.handle=wintypes.HANDLE(self.kernel.GetCurrentProcess());self.last=None
        class Memory(ctypes.Structure):
            _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(k,ctypes.c_size_t) for k in ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
        self.Memory=Memory;self.psapi=ctypes.WinDLL('psapi')
    def ticks(self,ft):return (ft.dwHighDateTime<<32)+ft.dwLowDateTime
    def cpu_seconds(self):
        values=[self.wt.FILETIME() for _ in range(4)]
        self.kernel.GetProcessTimes(self.handle,*[self.ct.byref(v) for v in values])
        return (self.ticks(values[2])+self.ticks(values[3]))/1e7
    def read(self):
        values=[self.wt.FILETIME() for _ in range(3)]
        self.kernel.GetSystemTimes(*[self.ct.byref(v) for v in values])
        idle,kernel,user=[self.ticks(v) for v in values];now=time.perf_counter();cpu=self.cpu_seconds()
        memory=self.Memory();memory.cb=self.ct.sizeof(memory)
        self.psapi.GetProcessMemoryInfo(self.handle,self.ct.byref(memory),memory.cb)
        result={'process_cpu_percent':0,'system_cpu_percent':0,'rss_mb':memory.WorkingSetSize/1048576}
        if self.last:
            old_at,old_cpu,old_idle,old_total=self.last;total=kernel+user-old_total
            result.update(process_cpu_percent=100*(cpu-old_cpu)/(now-old_at),system_cpu_percent=100*(1-(idle-old_idle)/total) if total else 0)
        self.last=(now,cpu,idle,kernel+user)
        return result

def write(name,value):
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf8')

def snapshot():
    from tools.p34_local import prepare
    prepare()
    from engine.config import settings
    from sqlalchemy import create_engine,text,select
    from engine import models as m
    db=create_engine(settings().database_url,isolation_level='AUTOCOMMIT',connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000'})
    at=int(time.time()*1000);data={'as_of':at,'tables':{},'ids':[]}
    with db.connect() as c:
        # Choose real observed contracts with long-enough native 5m and 1h
        # histories. Each index probe is bounded; never scan lifetime raw data.
        candidates=c.execute(text("select distinct o.instrument_id from observations o join instruments i on i.id=o.instrument_id where o.visible_at is not null and o.status<>'archived' and i.venue='binance' and i.quote_asset='USDT' order by o.instrument_id limit 60")).scalars().all()
        for iid in candidates:
            counts={tf:c.execute(text("select count(*) from (select 1 from candles where instrument_id=:iid and resolution=:tf and is_closed and close_time<=:at order by open_time desc limit :n) q"),{'iid':iid,'tf':tf,'at':at,'n':n}).scalar() for tf,n in [('5',1806),('60',300),('240',300),('1D',300)]}
            if counts['5']>=1800 and min(counts[t] for t in ('60','240','1D'))>=300:data['ids'].append(iid)
            if len(data['ids'])==12:break
        if len(data['ids'])<8:raise RuntimeError('Not enough representative complete-history observed contracts: '+str(data['ids']))
        ids=data['ids']
        data['tables']['instruments']=[dict(r) for r in c.execute(select(m.Instrument.__table__).where(m.Instrument.id.in_(ids))).mappings()]
        assets=[r['asset_id'] for r in data['tables']['instruments']]
        for table,query in [
            ('assets',select(m.Asset.__table__).where(m.Asset.id.in_(assets))),
            ('market_states',select(m.MarketState.__table__).where(m.MarketState.instrument_id.in_(ids))),
            ('identity_links',select(m.IdentityLink.__table__).where(m.IdentityLink.asset_id.in_(assets))),
            ('observations',select(m.Observation.__table__).where(m.Observation.instrument_id.in_(ids),m.Observation.visible_at.is_not(None),m.Observation.status!='archived').order_by(m.Observation.id).limit(150)),
            ('settings_versions',select(m.SettingsVersion.__table__).where(m.SettingsVersion.state=='published').order_by(m.SettingsVersion.id.desc()).limit(1)),
            ('user_preferences',select(m.UserPreference.__table__).where(m.UserPreference.key=='p37_config')),
        ]:data['tables'][table]=[dict(r) for r in c.execute(query).mappings()]
        candles=[];coverage=[]
        for iid in ids:
            for tf,n in [('5',1806),('60',400),('240',20000),('1D',4000),('1W',1000)]:
                rows=[dict(r) for r in c.execute(select(m.Candle.__table__).where(m.Candle.instrument_id==iid,m.Candle.resolution==tf,m.Candle.is_closed.is_(True),m.Candle.close_time<=at,m.Candle.received_at<=at).order_by(m.Candle.open_time.desc()).limit(n)).mappings()]
                candles.extend(rows);coverage.append({'iid':iid,'tf':tf,'copied':len(rows),'requested_limit':n})
        data['tables']['candles']=candles;data['coverage']=coverage
        data['queue_snapshot']=[dict(r) for r in c.execute(text("select kind,state,count(*) tasks,count(distinct payload->>'subject') subjects,min(available_at) oldest_available from tasks where kind in ('p39_structure','p39_trend') and coalesce((payload->>'shadow')::boolean,false)=false group by kind,state")).mappings()]
        data['analysis_states']=[dict(r) for r in c.execute(text("select work_kind,analysis_status,count(*) markets from market_analysis_states group by work_kind,analysis_status")).mappings()]
    db.dispose()
    with (OUT/'public-inputs.pickle').open('wb') as f:pickle.dump(data,f)
    write('input-manifest.json',{k:v for k,v in data.items() if k!='tables'}|{'scope':'bounded read-only production public market inputs; native closed histories; no production mutation','counts':{k:len(v) for k,v in data['tables'].items()},'raw_policy':'raw payload placeholders in isolated cluster retain original raw_ref identity; full exchange payloads not copied'})
    print(json.dumps({'phase':'snapshot','markets':len(ids),'candles':len(candles)},ensure_ascii=False),flush=True)

def seed(url):
    os.environ['ERJI_DATABASE_URL']=url;os.environ['ERJI_BUDGET_OWNER']='0';os.environ['ERJI_REQUIRE_AUTH']='false'
    from sqlalchemy import insert
    from engine.db import engine,Base,Session
    from engine import models as m
    from engine.repository import bootstrap
    from engine.p39.pipeline import upsert
    data=pickle.loads((OUT/'public-inputs.pickle').read_bytes());tables=data['tables'];at=data['as_of']
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        for name in ('assets','instruments','identity_links'):
            if tables[name]:c.execute(Base.metadata.tables[name].insert(),tables[name])
        refs={r['raw_ref'] for r in tables['candles']}
        c.execute(m.RawEvent.__table__.insert(),[{'id':ref,'source':'binance','endpoint':'capacity:public-candle-copy','received_at':data['as_of'],'payload':{'fixture':'public candle copy; original raw payload omitted'}} for ref in refs])
        for start in range(0,len(tables['candles']),2000):c.execute(m.Candle.__table__.insert(),tables['candles'][start:start+2000])
        for name in ('market_states','observations','settings_versions','user_preferences'):
            rows=tables[name]
            if name=='settings_versions':
                for row in rows:
                    row['config']['notifications']={**row['config'].get('notifications',{}),'enabled':False}
                    row['config']['collector_enabled']=False
            if rows:c.execute(Base.metadata.tables[name].insert(),rows)
    bootstrap()
    with Session.begin() as s:
        for iid in data['ids']:upsert(s,iid,'manual','capacity:'+iid,at,at+3600000,{'fixture':'isolated observed-market replay'})
        for key in ('p37_active','p39_structure_active','p39_trend_active'):
            s.merge(m.UserPreference(key=key,updated_at=at,payload={'instrument_ids':data['ids'],'source':'capacity-isolated'}))
    engine.dispose()

async def replay(url,concurrency,label):
    os.environ['ERJI_DATABASE_URL']=url;os.environ['ERJI_BUDGET_OWNER']='0';os.environ['ERJI_REQUIRE_AUTH']='false'
    data=pickle.loads((OUT/'public-inputs.pickle').read_bytes())
    # Keep source-freshness/closed-bar boundaries identical across all trials.
    # Real duration, resource sampling and SQL timing use perf_counter, not this
    # frozen business clock. No production process sees this monkeypatch.
    fixture_at=data['as_of'];time.time=lambda:fixture_at/1000;time.time_ns=lambda:fixture_at*1_000_000
    # Replay may only connect to its own isolated DB, never live DB/exchanges.
    from urllib.parse import urlsplit
    test_port=urlsplit(url.replace('postgresql+psycopg','postgresql')).port
    def audit(event,args):
        if event=='socket.connect':
            address=args[1]
            if not isinstance(address,tuple) or address[0] not in ('127.0.0.1','localhost','::1') or address[1]!=test_port:raise RuntimeError('capacity replay external connection blocked')
    sys.addaudithook(audit)
    from sqlalchemy import select,event,text
    from engine.db import engine,Session,metrics,_samples,_metric_lock
    from engine.models import Task,MarketState,UserPreference,AnalysisResult,Observation,WorkerHeartbeat
    from engine.p38 import jobs
    ids=data['ids'];at=fixture_at
    with Session.begin() as s:
        # The historical bars are copied unchanged. Refresh only fixture quote
        # clocks so the real scanner performs work instead of exiting stale.
        for row in s.scalars(select(MarketState)):
            row.as_of=at;row.payload={**row.payload,'as_of':at,'received_at':at,'capacity_fixture_quote_clock':True}
        for key in ('p37_active','p39_structure_active','p39_trend_active'):
            row=s.get(UserPreference,key);row.updated_at=at
        # Match a running production DB where this shared heartbeat exists.
        # The exploratory new-row race remains saved separately as a finding.
        for role in ('signals','decisions','signal_projection','signal_coverage'):
            if not s.get(WorkerHeartbeat,role):
                s.add(WorkerHeartbeat(role=role,updated_at=at,state='ok',details={'fixture':'pre-existing heartbeat for steady concurrency comparison'}))
    for kind in ('structure','trend'):jobs.submit(ids,'capacity',work_kind=kind)
    with _metric_lock:
        for q in _samples.values():q.clear()
    sql={'calls':0,'seconds':0,'max_ms':0};sql_lock=threading.Lock()
    @event.listens_for(engine,'before_cursor_execute')
    def before(conn,cursor,statement,parameters,context,executemany):context.capacity_start=time.perf_counter()
    @event.listens_for(engine,'after_cursor_execute')
    def after(conn,cursor,statement,parameters,context,executemany):
        elapsed=time.perf_counter()-context.capacity_start
        with sql_lock:sql['calls']+=1;sql['seconds']+=elapsed;sql['max_ms']=max(sql['max_ms'],elapsed*1000)
    class NoNetwork:
        async def collect(self,*args,**kwargs):raise RuntimeError('unexpected network attempt')
    adapter=NoNetwork();tasks=[];records=[];active_counts={'structure':0,'trend':0};peak_counts={'structure':0,'trend':0,'total':0};begin=time.perf_counter();probe=ResourceProbe();cpu0=probe.cpu_seconds();stop=threading.Event();resource=[]
    probe.read()
    def sample():
        while not stop.wait(.5):
            resource.append({'elapsed':time.perf_counter()-begin,**probe.read()})
    monitor=threading.Thread(target=sample,daemon=True);monitor.start()
    async def lane(kind):
        while True:
            lease=await asyncio.to_thread(jobs.take,work_kinds=(kind,))
            if lease is None:return
            started=time.perf_counter()
            active_counts[kind]+=1;peak_counts[kind]=max(peak_counts[kind],active_counts[kind]);peak_counts['total']=max(peak_counts['total'],sum(active_counts.values()))
            await jobs.execute(lease,adapter)
            active_counts[kind]-=1
            with Session() as s:
                row=s.get(Task,lease['id']);p=dict(row.payload)
            records.append({'kind':kind,'iid':lease['instrument_id'],'elapsed_ms':round((time.perf_counter()-started)*1000,2),'state':row.state,'error':p.get('error'),'deferred_reason':p.get('deferred_reason'),'completed_count':p.get('completed_count',0)})
            # A failed/deferred input is evidence, not an unlimited retry loop.
            if row.state!='complete':return
    try:await asyncio.wait_for(asyncio.gather(*(lane(kind) for kind in ('structure','trend') for _ in range(concurrency))),timeout=180)
    finally:stop.set();monitor.join(timeout=2)
    elapsed=time.perf_counter()-begin;cpu1=probe.cpu_seconds()
    with Session() as s:
        outcomes=[{'kind':r.work_kind,'status':r.payload.get('status'),'summary':r.payload.get('summary'),'error':r.payload.get('error')} for r in s.scalars(select(AnalysisResult))]
        finished=[{'kind':kind,'state':state,'count':count} for kind,state,count in s.execute(text('select kind,state,count(*) from tasks group by kind,state'))]
        deadlocks=s.execute(text('select deadlocks from pg_stat_database where datname=current_database()')).scalar()
    good=sum(r['state']=='complete' and not r['error'] for r in records)
    result={'label':label,'business_clock_frozen_at':fixture_at,'per_kind_concurrency':concurrency,'total_analysis_concurrency':concurrency*2,'markets':len(ids),'intended_tasks':len(ids)*2,'elapsed_seconds':round(elapsed,3),'completed_tasks':good,'tasks_per_minute':round(good/elapsed*60,3),'process_cpu_seconds':round(cpu1-cpu0,3),'resources':{'system_cpu_mean':round(statistics.mean(r['system_cpu_percent'] for r in resource),2) if resource else None,'system_cpu_max':max((r['system_cpu_percent'] for r in resource),default=None),'process_cpu_mean':round(statistics.mean(r['process_cpu_percent'] for r in resource),2) if resource else None,'rss_peak_mb':round(max((r['rss_mb'] for r in resource),default=0),1)},'database_timings':metrics(),'sql':sql,'deadlocks':deadlocks,'task_records':records,'outcomes':outcomes,'states':finished,'scope':'real jobs.execute, isolated public candle snapshot, no collector/producer/live quote writes, cold process caches, fixed business clock and fixture quote clocks, no prior episodes/plans copied'}
    result['observed_peak_inflight']=peak_counts
    write(label+'.json',result);print(json.dumps({k:v for k,v in result.items() if k not in ('task_records','outcomes')},ensure_ascii=False),flush=True)
    engine.dispose()

def orchestrate(extend=False,confirm=False):
    if not (OUT/'public-inputs.pickle').exists():snapshot()
    pg=Path('D:/PostgreSQL/bin');cluster=ROOT/'.test-tmp'/('capacity-pg-'+str(time.time_ns()));cluster.mkdir(parents=True)
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    def pg_run(name,*args):
        with (OUT/(name+'-command.log')).open('ab') as log:
            result=subprocess.run([str(pg/(name+'.exe')),*map(str,args)],stdout=log,stderr=log,stdin=subprocess.DEVNULL,creationflags=FLAGS,timeout=60)
        if result.returncode:raise RuntimeError(name+' failed; inspect command log')
    def child(*args,timeout=240):
        env=os.environ.copy();env['ERJI_BUDGET_OWNER']='0';env['PGCONNECT_TIMEOUT']='5'
        result=subprocess.run([sys.executable,'-X','utf8',str(Path(__file__)),*args],env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,encoding='utf8',errors='replace',creationflags=FLAGS,timeout=timeout)
        (OUT/(args[-1]+'.log' if args[0]=='run' else 'seed.log')).write_text(result.stdout,encoding='utf8')
        if result.returncode:raise RuntimeError(result.stdout[-3500:])
        print(result.stdout[-1500:],flush=True)
    if not extend and (OUT/'results.json').exists():
        write('exploratory-results-'+str(time.time_ns())+'.json',json.loads((OUT/'results.json').read_text('utf8')))
    runs=json.loads((OUT/'results.json').read_text('utf8'))['runs'] if extend else []
    try:
        pg_run('initdb','-D',cluster,'-U','capacity','--encoding=UTF8','--locale=C','--auth=trust')
        pg_run('pg_ctl','-D',cluster,'-l',cluster/'server.log','-o',f'-h 127.0.0.1 -p {port} -c shared_buffers=128MB -c max_connections=60','-w','start')
        import psycopg
        def admin(query):
            with psycopg.connect(host='127.0.0.1',port=port,user='capacity',dbname='postgres',autocommit=True) as c:c.execute(query)
        admin('CREATE DATABASE capacity_template')
        child('seed',f'postgresql+psycopg://capacity@127.0.0.1:{port}/capacity_template')
        for index,n in enumerate((2,8,1,4) if confirm else (8,8) if extend else (1,2,4,8,8,4,2,1),start=len(runs)):
            name='capacity_'+str(index);label=f'{"confirm" if confirm else "steady"}-{index:02d}-c{n}'
            admin('CREATE DATABASE '+name+' TEMPLATE capacity_template')
            child('run',f'postgresql+psycopg://capacity@127.0.0.1:{port}/{name}',str(n),label)
            result=json.loads((OUT/(label+'.json')).read_text('utf8'));runs.append(result)
            admin('DROP DATABASE '+name+' WITH (FORCE)')
            write('results.json',{'scope':'isolated finite capacity replay; not production sustained capacity','runs':runs})
            if result['completed_tasks']!=result['intended_tasks']:break
    finally:
        if (cluster/'postmaster.pid').exists():pg_run('pg_ctl','-D',cluster,'-m','fast','-w','stop')
        write('isolation.json',{'production_writes':False,'exchange_calls':False,'cluster':str(cluster),'port':port,'stopped':not (cluster/'postmaster.pid').exists(),'completed_runs':len(runs)})

if __name__=='__main__':
    if len(sys.argv)==1:orchestrate()
    elif sys.argv[1]=='extend':orchestrate(extend=True)
    elif sys.argv[1]=='confirm':orchestrate(extend=True,confirm=True)
    elif sys.argv[1]=='seed':seed(sys.argv[2])
    elif sys.argv[1]=='run':asyncio.run(replay(sys.argv[2],int(sys.argv[3]),sys.argv[4]))
