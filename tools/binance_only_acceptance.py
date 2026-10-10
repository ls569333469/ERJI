"""One bounded, read-only local acceptance sample. Never contacts an exchange."""
import sys,json,time,socket,urllib.request,re,os
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.p34_local import prepare
prepare()
from sqlalchemy import create_engine,text
from engine.config import settings
OUT=ROOT/os.environ.get('ERJI_ACCEPTANCE_OUT','docs/p39-validation/binance-only-implementation');OUT.mkdir(parents=True,exist_ok=True)
STATE=OUT/'finite-acceptance-state.json'
old=json.loads(STATE.read_text('utf8')) if STATE.exists() else {}
registry=json.loads((ROOT/'.runtime/processes.json').read_text('utf-8-sig'))
run=next(p['run_id'] for p in registry if p['name']=='worker-jobs')
if old.get('run_id')!=run:old={'run_id':run,'offsets':{},'samples':[]}
at=int(time.time()*1000);activated=datetime.strptime(run.split('-')[0],'%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc)
since=int(activated.timestamp()*1000)
sample={'sampled_at_utc':datetime.now(timezone.utc).isoformat(),'as_of':at,'run_id':run,'since_activation_ms':at-since,'read_only':True,'ports':{},'db':{},'http':{},'logs':[]}
for port in (18770,18771,18772,55432):
    with socket.socket() as s:s.settimeout(1);sample['ports'][str(port)]=s.connect_ex(('127.0.0.1',port))==0
queries={
 'fx':"select key,status,observed_at,received_at,last_attempt_at,expires_at,payload,reason,raw_ref from data_capabilities where key in ('binance:fx:USDTUSD','binance:fx:USDCUSD')",
 'tasks':"select kind,state,count(*) n,sum(coalesce((payload->>'completed_count')::int,0)) completed,sum(coalesce((payload->>'failed_count')::int,0)) failed,max((payload->>'finished_at')::bigint) finished_at from tasks where kind like 'p39_%' group by 1,2 order by 1,2",
 'failed_tasks':"select kind,payload->>'subject' subject,payload->>'error' error,payload->>'stage' stage,(payload->>'finished_at')::bigint finished_at from tasks where state='failed' and kind like 'p39_%' order by (payload->>'finished_at')::bigint desc limit 10",
 'lanes':"select key,updated_at,payload from user_preferences where key in ('p39_structure_active','p39_trend_active','p37_active')",
 'source_fields':"select i.venue,count(*) n,count(*) filter(where :at-m.as_of between 0 and 15000) quotes_15s,count(*) filter(where :at-m.as_of between 0 and 360000) quotes_6m,count(*) filter(where :at-(m.payload->>'oi_as_of')::bigint between 0 and 90000) oi_90s,count(*) filter(where :at-(m.payload->>'oi_as_of')::bigint between 0 and 360000) oi_6m,count(*) filter(where m.payload->>'funding_rate' is not null) funding_present from instruments i join market_states m on m.instrument_id=i.id where i.venue in ('binance','binance_spot','binance_alpha') group by 1",
 'funding_provenance':"select i.venue,m.payload->>'funding_kind' kind,m.payload->>'funding_status' status,count(*) n,min((m.payload->>'funding_settled_at')::bigint) earliest_settled_at,max((m.payload->>'funding_settled_at')::bigint) latest_settled_at from instruments i join market_states m on m.instrument_id=i.id where i.venue='binance' group by 1,2,3",
 'observation_kline_windows':"""with targets as (select distinct o.instrument_id from observations o join instruments i on i.id=o.instrument_id where o.visible_at is not null and o.status!='archived' and i.venue in ('binance','binance_spot','binance_alpha')), frames(tf,step,off) as (values ('60',3600000::bigint,0::bigint),('240',14400000::bigint,0::bigint),('1D',86400000::bigint,0::bigint),('1W',604800000::bigint,345600000::bigint)), windows as (select t.instrument_id,f.tf,f.step,f.off,count(c.open_time) bars,min(c.open_time) first,max(c.open_time) last,max(c.close_time) closed from targets t cross join frames f left join lateral (select open_time,close_time from candles where instrument_id=t.instrument_id and resolution=f.tf and is_closed and close_time<=:at order by open_time desc limit 300) c on true group by 1,2,3,4) select tf,count(*) markets,count(*) filter(where bars=0) absent,count(*) filter(where bars>0 and last-first=(bars-1)*step) contiguous_returned_window,count(*) filter(where bars>0 and last-first!=(bars-1)*step) gaps_in_returned_window,count(*) filter(where closed=(:at-off)/step*step+off) latest_expected_closed,count(*) filter(where bars<100) bars_below100 from windows group by tf order by tf""",
 'observation_progress':"select count(distinct instrument_id) markets,count(distinct instrument_id) filter(where (payload->>'structure_checked_at')::bigint>=:since) structure_checked,count(distinct instrument_id) filter(where (payload->>'analysis_checked_at')::bigint>=:since) trend_checked,count(distinct instrument_id) filter(where (payload->>'structure_last_attempt_at')::bigint>=:since) structure_attempted from observations where visible_at is not null and status!='archived'",
 'structure_reasons':"select payload->>'structure_check_status' reason,count(distinct instrument_id) markets from observations where status!='archived' and (payload->>'structure_last_attempt_at')::bigint>=:since group by 1",
 'structure_input_gaps':"select distinct instrument_id,payload->>'structure_check_status' reason,(payload->>'structure_last_attempt_at')::bigint last_attempt_at,(payload->>'structure_next_retry_at')::bigint retry_at from observations where visible_at is not null and status!='archived' and (payload->>'structure_last_attempt_at')::bigint>=:since and payload->>'structure_check_status'!='checked'",
 'historical_buy_proof':"select id,as_of,payload from signal_snapshots where id='p33-ca253c60301b86a7ea82d62d:p33:1791357598318'",
 'forbidden_new_raw':"select source,count(*) n,max(received_at) latest from raw_events where source in ('aster','hyperliquid','cmc','bykaranteli','okx','geckoterminal') and received_at>=:since group by 1",
 'external_capability_attempts':"select provider,count(*) n,max(last_attempt_at) latest from data_capabilities where provider in ('aster','hyperliquid','cmc','bykaranteli','okx','geckoterminal') and last_attempt_at>=:since group by 1",
 'external_ws_open':"select key,updated_at from user_preferences where key like 'ws_coverage:%' and split_part(key,':',2) in ('aster','hyperliquid','okx') and payload->>'connected'='true'",
 'heartbeats':"select role,state,updated_at,details from worker_heartbeats where details->>'run_id'=:run and (role in ('analysis_owner','signal_projection','anomaly_discovery','observation_analysis','supplements','api','live','jobs','streams') or role like 'stream_binance%')",
 'projection':"select count(*) rows,max(computed_at) latest,count(*) filter(where valid_until>:at and action in ('trial','participate')) current_buy from current_signals",
 'external_current_buy':"select c.id,c.instrument_id,c.action,c.valid_until from current_signals c join instruments i on i.id=c.instrument_id where i.venue not in ('binance','binance_spot','binance_alpha') and c.action in ('trial','participate') and c.valid_until>:at",
 'kline_btc':"select resolution,count(*) n,max(close_time) latest_closed,max(received_at) latest_received from candles where instrument_id='binance:perp:BTCUSDT' and is_closed and close_time<=:at group by 1",
 'index':"select indexname,indisvalid from pg_indexes p join pg_class c on c.relname=p.indexname join pg_index i on i.indexrelid=c.oid where p.schemaname='public' and p.indexname='ix_signal_snapshots_plan_latest'",
 'policy_config':"select id,config->'sources' sources from settings_versions where state='published' order by id desc limit 1",
 'identity_coverage':"select provider,status,count(*) n from identity_links group by 1,2",
 'source_health':"select source,state,checked_at,last_success_at,message from source_health order by source",
 'ledger':"select updated_at,payload->'buckets' buckets,payload->>'binance_ban_until' ban_until,json_array_length(coalesce(payload->'pending','[]'::json)) pending from user_preferences where key='p36_http_ledger'",
}
db=create_engine(settings().database_url,isolation_level='AUTOCOMMIT',connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=8000 -c lock_timeout=1000'})
try:
    with db.connect() as conn:
        for key,query in queries.items():
            start=time.monotonic()
            try:sample['db'][key]=[dict(r) for r in conn.execute(text(query),{'at':at,'since':since,'run':run}).mappings()]
            except Exception as exc:sample['db'][key]={'error':type(exc).__name__,'detail':str(exc).split('[SQL:')[0][:250]}
finally:db.dispose()
endpoints={'health':'http://127.0.0.1:18771/v1/health','observations':'http://127.0.0.1:18771/v1/anomaly-observations?limit=300','signals':'http://127.0.0.1:18771/v1/signals?view=opportunities&page_size=300','sources':'http://127.0.0.1:18771/v1/sources','coverage':'http://127.0.0.1:18771/v1/coverage','budget':'http://127.0.0.1:18772/health','web':'http://127.0.0.1:18770/signals'}
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
for key,url in endpoints.items():
    start=time.monotonic()
    try:
        with opener.open(url,timeout=15) as response:
            data=response.read(16000000);row={'status':response.status,'ms':round((time.monotonic()-start)*1000),'bytes':len(data)}
            if 'json' in response.headers.get('Content-Type',''):
                body=json.loads(data);row['as_of']=body.get('as_of');row['rows']=len(body.get('rows',[]))
                if key=='observations':
                    rows=body.get('rows',[]);row.update(unique_assets=body.get('unique_assets'),status_divergence=sum(r.get('task_status')=='ready' and r.get('analysis_status')=='delayed' for r in rows),structure_statuses={status:sum(r.get('structure_task_status')==status for r in rows) for status in set(r.get('structure_task_status') for r in rows)},counts=body.get('counts'),representative_structure_checked_this_run=sum((r.get('structure_checked_at') or 0)>=since for r in rows),representative_trend_analyzed_this_run=sum((r.get('analyzed_at') or 0)>=since for r in rows),inapplicable_representatives=sum(r.get('structure_task_status')=='not_applicable' for r in rows))
                if key=='signals':
                    row.update(pagination=body.get('pagination'),levels={level:sum(r.get('opportunity_level')==level for r in body.get('rows',[])) for level in ('l1','l2','l3')},current_buy_samples=[r for r in body.get('rows',[]) if r.get('opportunity_level')=='l1'])
                if key=='health':row.update(source_policy=body.get('source_policy'),execution_status=body.get('execution_status'))
                if key=='sources':row['sources']=[{'source':r['source'],'state':r['state']} for r in body.get('rows',[])]
                if key in ('observations','signals','sources','coverage'):(OUT/(f'{run}-{key}-latest.json')).write_text(json.dumps(body,ensure_ascii=False,indent=2),encoding='utf8')
            sample['http'][key]=row
    except Exception as exc:sample['http'][key]={'error':type(exc).__name__,'detail':str(exc)[:150]}
offsets=old['offsets']
for proc in registry:
    if proc['name']=='web':continue
    for suffix in ('err','out'):
        path=Path(proc['log_dir'])/(proc['name']+'.'+suffix+'.log')
        if not path.exists():continue
        size=path.stat().st_size;start=offsets.get(str(path),0 if proc['run_id']==run else size)
        with path.open('rb') as f:f.seek(max(start,size-524288));data=f.read(524288).decode('utf8',errors='replace')
        # A timestamp millisecond or commit_ms=429.2 is not an HTTP failure.
        patterns={k:len(re.findall(v,data,re.I)) for k,v in {'tracebacks':'Traceback','429':r'(?:HTTP(?:/\S+)?|status(?:_code)?|response(?:\s+status)?)[\s:=\"\']+429\b','418':r'(?:HTTP(?:/\S+)?|status(?:_code)?|response(?:\s+status)?)[\s:=\"\']+418\b','timeouts':'TimeoutError|timed out','role_lost':'worker_role_lost','connection_held':'database connection held'}.items()}
        excerpt='\n'.join(data.splitlines()[-12:]) if any(patterns.values()) else ''
        sample['logs'].append({'name':proc['name'],'suffix':suffix,'bytes_added':size-start,'patterns':patterns,'excerpt':excerpt})
        offsets[str(path)]=size
totals={}
for row in sample['db'].get('tasks',[]) if isinstance(sample['db'].get('tasks'),list) else []:
    item=totals.setdefault(row['kind'],{'completed':0,'failed':0});item['completed']+=row['completed'] or 0;item['failed']+=row['failed'] or 0
baseline=old.get('task_baseline',totals)
sample['task_delta']={k:{field:v[field]-baseline.get(k,{}).get(field,0) for field in ('completed','failed')} for k,v in totals.items()}
path=OUT/(f'acceptance-{run}-{len(old["samples"]):02}.json');path.write_text(json.dumps(sample,ensure_ascii=False,indent=2,default=str),encoding='utf8')
old.update(offsets=offsets,task_baseline=baseline,last_sample=str(path),last_sampled_at=sample['sampled_at_utc']);old['samples'].append(str(path))
STATE.write_text(json.dumps(old,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'path':str(path),'elapsed_minutes':round((at-since)/60000,2),'ports':sample['ports'],'http':sample['http'],'task_delta':sample['task_delta'],'progress':sample['db'].get('observation_progress'),'forbidden_new_raw':sample['db'].get('forbidden_new_raw'),'failures':sample['db'].get('failed_tasks'),'fx':[{k:r.get(k) for k in ('key','status','observed_at','reason')} for r in sample['db'].get('fx',[])],'new_log_errors':[r for r in sample['logs'] if any(r['patterns'].values())]},ensure_ascii=False,default=str))
