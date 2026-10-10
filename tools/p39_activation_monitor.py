"""Light, read-only local monitoring. Persist samples and log offsets only."""
import sys,json,time,socket,re,urllib.request,os
from pathlib import Path
from datetime import datetime,timezone,timedelta
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.p34_local import prepare
prepare()
from sqlalchemy import create_engine,text
from engine.config import settings
OUT=ROOT/'docs/p39-validation';STATE=OUT/'activation-monitor-state.json'
old=json.loads(STATE.read_text('utf8')) if STATE.exists() else {}
processes=json.loads((ROOT/'.runtime/processes.json').read_text('utf-8-sig'))
run=next(p['run_id'] for p in processes if p['name']=='worker-jobs')
now=int(time.time()*1000);stamp=datetime.now(timezone(timedelta(hours=8))).isoformat()
sample={'sampled_at_bjt':stamp,'as_of_ms':now,'run_id':run,'read_only':True,'ports':{},'queries':{},'http':{},'logs':[]}
def process_identity(row):
    result={'name':row['name'],'pid':row['pid'],'run_id':row['run_id']}
    if os.name!='nt':return {**result,'error':'windows_identity_unavailable'}
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
    kernel.GetProcessTimes.restype=wintypes.BOOL
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x1000,False,row['pid'])
    if not handle:return {**result,'identity_matches':False,'error':'process_missing_or_inaccessible','winerror':ctypes.get_last_error()}
    try:
        created,exited,kernel_time,user_time=(wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle,ctypes.byref(created),ctypes.byref(exited),ctypes.byref(kernel_time),ctypes.byref(user_time)):
            return {**result,'identity_matches':False,'error':'process_time_inaccessible','winerror':ctypes.get_last_error()}
        # FILETIME is 100ns since 1601; registry ticks are .NET since 0001.
        ticks=((created.dwHighDateTime<<32)|created.dwLowDateTime)+504911232000000000
        return {**result,'identity_matches':str(ticks)==str(row.get('started_ticks')),'started_ticks':str(ticks)}
    finally:kernel.CloseHandle(handle)
sample['process_identity']=[process_identity(row) for row in processes]
if old.get('run_id')!=run:
    # Preserve notification history, but never use old-run counters/offsets as
    # a new-run baseline. Saved prior samples remain the detailed archive.
    prior_run=old.get('run_id')
    old={'run_id':run,'previous_run_id':prior_run,'offsets':{},'baseline':None,
         'notified_issues':old.get('notified_issues',[])}
    sample['baseline_rebuilt']=True
for port in (18770,18771,18772,55432):
    with socket.socket() as s:
        s.settimeout(1);sample['ports'][str(port)]=s.connect_ex(('127.0.0.1',port))==0
db=create_engine(settings().database_url,isolation_level='AUTOCOMMIT',connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000'})
assert db.url.host in ('localhost','127.0.0.1') and db.url.port==55432 and db.url.database=='erji'
run_start=int(datetime.strptime(run.split('-')[0],'%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc).timestamp()*1000)
queries={
 'schema':"select version_num from alembic_version",
 'index':"select indexname,indisvalid from pg_indexes p join pg_class c on c.relname=p.indexname join pg_index i on i.indexrelid=c.oid where p.schemaname='public' and p.indexname='ix_signal_snapshots_plan_latest'",
 'tasks':"select kind,state,count(*) n,sum(coalesce((payload->>'completed_count')::int,0)) completed,sum(coalesce((payload->>'failed_count')::int,0)) failed,max((payload->>'finished_at')::bigint) latest_finish from tasks where kind like 'p39_%' group by 1,2 order by 1,2",
 'failures':"select kind,payload->>'subject' subject,payload->>'error' error,(payload->>'finished_at')::bigint finished_at from tasks where kind like 'p39_%' and state='failed' and (payload->>'started_at')::bigint>=:start order by (payload->>'finished_at')::bigint desc limit 6",
 'projection':"select count(*) n,max(computed_at) latest_projection,count(*) filter(where action in ('trial','participate') and valid_until>:at) unexpired_buy from current_signals",
 'source':"select i.venue,count(*) markets,count(*) filter(where :at-m.as_of between 0 and 15000) quotes_15s,count(*) filter(where :at-m.as_of between 0 and 60000) quotes_60s,count(*) filter(where :at-(m.payload->>'oi_as_of')::bigint between 0 and 90000) oi_90s,count(*) filter(where :at-(m.payload->>'oi_as_of')::bigint between 0 and 360000) oi_6m from instruments i join market_states m on m.instrument_id=i.id where i.venue in ('binance','binance_spot') group by 1",
 'beats':"select role,state,updated_at,details from worker_heartbeats where details->>'run_id'=:run and (role in ('analysis_owner','signal_projection','anomaly_discovery','api','jobs','live','streams') or role like 'stream_binance%')",
 'candles':"select resolution,max(received_at) latest_received,max(close_time) latest_closed from candles where instrument_id='binance:perp:BTCUSDT' and is_closed=true and close_time<=:at group by 1",
 'budget':"select updated_at,payload->>'binance_ban_until' ban_until,json_array_length(coalesce(payload->'pending','[]'::json)) pending from user_preferences where key='p36_http_ledger'",
}
try:
    with db.connect() as c:
        for key,query in queries.items():
            try:sample['queries'][key]=[dict(r) for r in c.execute(text(query),{'at':now,'run':run,'start':run_start}).mappings()]
            except Exception as exc:sample['queries'][key]={'error':type(exc).__name__}
finally:db.dispose()
for endpoint in ('http://127.0.0.1:18771/v1/health','http://127.0.0.1:18771/v1/signals?page_size=1','http://127.0.0.1:18772/health','http://127.0.0.1:18770/signals'):
    try:
        started=time.monotonic()
        with urllib.request.urlopen(endpoint,timeout=8) as response:
            result={'status':response.status,'ms':round((time.monotonic()-started)*1000)}
            if ':18771/' in endpoint:
                body=json.loads(response.read(200000));result['fields']={k:body[k] for k in ('as_of','status','total','count','unique_asset_count','cursor','next_cursor') if k in body}
                result['returned_rows']=len(body.get('rows',[]))
            sample['http'][endpoint]=result
    except Exception as exc:sample['http'][endpoint]={'error':type(exc).__name__}
offsets=old.get('offsets',{})
for p in processes:
    for log in [Path(p['log_dir'])/(p['name']+'.err.log')]:
        if not log.exists():continue
        key=str(log);size=log.stat().st_size
        # A retained web/budget process starts from this baseline, never import
        # its older run's errors into the freshly activated worker diagnosis.
        start=offsets.get(key,0 if p['run_id']==run else size)
        if size<start:start=0
        with log.open('rb') as f:
            f.seek(max(start,size-131072));raw=f.read(131072).decode('utf8',errors='replace')
        counts={k:len(re.findall(pattern,raw,re.I)) for k,pattern in {'traceback':'Traceback','rate_429':r'(?:HTTP(?:/\d(?:\.\d)?)?\s+|status(?:_code)?[\s=:]+)429\b','ban_418':r'(?:HTTP(?:/\d(?:\.\d)?)?\s+|status(?:_code)?[\s=:]+)418\b','timeout':'TimeoutError|timed out','connection_closed':'ConnectionClosedError','held_connection':'database connection held','runtime_error':'RuntimeError'}.items()}
        increment_path=OUT/('activation-log-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+p['name']+'.log')
        increment_path.write_text(raw,'utf8')
        sample['logs'].append({'path':key,'run_id':p['run_id'],'bytes_added':max(0,size-start),'truncated':size-start>131072,'new_patterns':counts,'saved_increment':str(increment_path)})
        offsets[key]=size
def totals(rows):
    out={}
    if isinstance(rows,list):
        for r in rows:
            item=out.setdefault(r['kind'],{'completed':0,'failed':0});item['completed']+=r['completed'] or 0;item['failed']+=r['failed'] or 0
    return out
total=totals(sample['queries'].get('tasks'));baseline=old.get('baseline') or {'sampled_at_bjt':stamp,'totals':total}
sample['since_baseline']={k:{f:total[k][f]-baseline['totals'].get(k,{}).get(f,0) for f in ('completed','failed')} for k in total}
path=OUT/('activation-sample-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
path.write_text(json.dumps(sample,ensure_ascii=False,indent=2,default=str),encoding='utf8')
with (OUT/'activation-observations.jsonl').open('a',encoding='utf8') as f:f.write(json.dumps(sample,ensure_ascii=False,default=str)+'\n')
old.update(run_id=run,offsets=offsets,baseline=baseline,last_sample=str(path),last_sampled_at_bjt=stamp)
STATE.write_text(json.dumps(old,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'path':str(path),'ports':sample['ports'],'queries':sample['queries'],'since_baseline':sample['since_baseline'],'http':sample['http'],'logs':sample['logs']},ensure_ascii=False,default=str))
