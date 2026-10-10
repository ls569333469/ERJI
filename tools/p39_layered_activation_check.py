"""Local, read-only activation evidence; never requests exchange data."""
import hashlib
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from p34_local import prepare
prepare()
from sqlalchemy import text
from engine.db import engine

ROOT = Path.cwd()
OUT = ROOT / 'docs/p39-validation/layered-analysis-implementation-20261007/activation'
OUT.mkdir(parents=True, exist_ok=True)
phase = sys.argv[1] if len(sys.argv) > 1 else 'check'
evidence = json.loads((OUT.parent / 'implementation-evidence.json').read_text(encoding='utf-8-sig'))
supplement = json.loads((OUT/'epoch-fix-manifest.json').read_text(encoding='utf-8-sig')) if (OUT/'epoch-fix-manifest.json').exists() else {}
mismatches = [item['path'] for item in evidence['changed_files'] if hashlib.sha256((ROOT/item['path']).read_bytes()).hexdigest() != supplement.get(item['path'],item['after_sha256'])]
target = {'host':engine.url.host,'port':engine.url.port,'database':engine.url.database}
assert target == {'host':'127.0.0.1','port':55432,'database':'erji'} or target == {'host':'localhost','port':55432,'database':'erji'}, target
result = {'checked_at':datetime.now(timezone.utc).isoformat(),'phase':phase,'database':target,'code_hash_mismatches':mismatches,'layered_flag_enabled':os.getenv('ERJI_P39_LAYERED_ANALYSIS','1').lower() not in ('0','false','off'),'processes':json.loads((ROOT/'.runtime/processes.json').read_text(encoding='utf-8-sig'))}
with engine.connect() as conn:
    conn.execute(text('SET TRANSACTION READ ONLY'))
    conn.execute(text("SET LOCAL statement_timeout='10s'"))
    result['revision'] = conn.execute(text('select version_num from alembic_version')).scalars().all()
    result['tables'] = conn.execute(text("select table_name from information_schema.tables where table_schema='public' and table_name in ('analysis_demands','analysis_results','market_analysis_states')")).scalars().all()
    result['indexes'] = [dict(r) for r in conn.execute(text("select c.relname as name,t.relname as table_name,i.indisvalid as valid from pg_index i join pg_class c on c.oid=i.indexrelid join pg_class t on t.oid=i.indrelid where t.relname in ('analysis_demands','analysis_results','market_analysis_states') or c.relname='ix_signal_snapshots_plan_latest'")).mappings()]
    result['heartbeats'] = [dict(r) for r in conn.execute(text('select role,state,updated_at,details from worker_heartbeats')).mappings()]
    result['task_counts'] = [dict(r) for r in conn.execute(text("select kind,state,count(*) as count,sum(coalesce((payload->>'completed_count')::bigint,0)) as completed,sum(coalesce((payload->>'failed_count')::bigint,0)) as failed from tasks where kind like 'p39_%' group by kind,state")).mappings()]
    result['task_errors'] = [dict(r) for r in conn.execute(text("select kind,payload->>'subject' as subject,payload->>'error' as error,payload->>'finished_at' as finished_at from tasks where kind like 'p39_%' and state='failed' limit 10")).mappings()]
    if len(result['tables']) == 3:
        result['demands'] = [dict(r) for r in conn.execute(text('select origin_type,state,count(*) as count from analysis_demands group by origin_type,state')).mappings()]
        result['states'] = [dict(r) for r in conn.execute(text('select work_kind,analysis_status,strategy_result,count(*) as count from market_analysis_states group by work_kind,analysis_status,strategy_result')).mappings()]
        result['recent_results'] = [dict(r) for r in conn.execute(text('select id,instrument_id,work_kind,input_version,computed_at,payload from analysis_results order by computed_at desc limit 8')).mappings()]
if phase != 'preflight':
    result['http'] = {}
    urls = ['/v1/health','/v1/anomaly-observations','/v1/signals?view=opportunities&page_size=5']
    if result.get('recent_results'):
        from urllib.parse import quote
        urls.append('/v1/analysis-pipeline/'+quote(result['recent_results'][0]['instrument_id'],safe='')+'?limit=5')
    for path in urls:
        try:
            with urllib.request.urlopen('http://127.0.0.1:18771'+path,timeout=15) as response:
                result['http'][path] = {'status':response.status,'body':json.load(response)}
        except Exception as exc:
            result['http'][path] = {'error':str(exc)}
    for port,path in [(18770,'/signals'),(18772,'/health')]:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}',timeout=30) as response:
                result['http'][f'{port}{path}']={'status':response.status}
        except Exception as exc:
            result['http'][f'{port}{path}']={'error':str(exc)}
(OUT/f'{phase}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
summary = {k:v for k,v in result.items() if k not in ('recent_results','http','processes','heartbeats')}
run_id=result['processes'][0]['run_id']
summary['current_run_workers']=[{'role':r['role'],'state':r['state'],'age_ms':int(time.time()*1000)-r['updated_at']} for r in result['heartbeats'] if r['details'].get('run_id')==run_id]
summary['analysis_owner'] = next((r['details'] for r in result['heartbeats'] if r['role']=='analysis_owner' and r['details'].get('run_id')==run_id),{})
summary['http'] = {k:{key:value for key,value in v.items() if key!='body'} for k,v in result.get('http',{}).items()}
print(json.dumps(summary,ensure_ascii=False,indent=2,default=str))
assert not mismatches, mismatches
assert result['layered_flag_enabled'], 'Layered analysis disabled'
