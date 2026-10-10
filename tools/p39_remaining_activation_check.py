"""One-shot, read-only activation evidence; does not resume monitoring."""
import hashlib, json, re, socket, sys, time, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.p34_local import prepare
prepare()
from sqlalchemy import create_engine, text
from engine.config import settings

out = ROOT / 'docs/p39-validation'
processes = json.loads((ROOT / '.runtime/processes.json').read_text('utf-8-sig'))
run = next(p['run_id'] for p in processes if p['name'] == 'worker-jobs')
startup = json.loads((ROOT / '.runtime/runs' / run / 'startup.json').read_text('utf-8-sig'))
at = int(time.time() * 1000)
stamp = datetime.now(timezone(timedelta(hours=8))).isoformat()
sample = {'checked_at_bjt': stamp, 'run_id': run, 'startup': startup,
          'read_only_database': True, 'monitor_status': 'PAUSED', 'ports': {}, 'http': {}, 'queries': {}, 'logs': []}
manifest = json.loads((out / 'remaining-code-manifest.json').read_text('utf8'))['sha256']
sample['source_manifest_matches'] = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h for p, h in manifest.items()}
for port in (18770, 18771, 18772, 55432):
    with socket.socket() as sock:
        sock.settimeout(1)
        sample['ports'][str(port)] = sock.connect_ex(('127.0.0.1', port)) == 0
for endpoint in ('http://127.0.0.1:18771/v1/health', 'http://127.0.0.1:18771/v1/signals?page_size=5',
                 'http://127.0.0.1:18772/health', 'http://127.0.0.1:18770/signals'):
    try:
        start = time.monotonic()
        with urllib.request.urlopen(endpoint, timeout=8) as response:
            item = {'status': response.status, 'ms': round((time.monotonic() - start) * 1000)}
            if ':18771/' in endpoint:
                body = json.loads(response.read(300000))
                item['fields'] = {k: body[k] for k in ('status', 'as_of', 'total', 'unique_asset_count', 'workers') if k in body}
                item['returned_rows'] = len(body.get('rows', []))
            sample['http'][endpoint] = item
    except Exception as exc:
        sample['http'][endpoint] = {'error': type(exc).__name__}
queries = {
 'index': "select indexname,indisvalid from pg_indexes p join pg_class c on c.relname=p.indexname join pg_index i on i.indexrelid=c.oid where p.schemaname='public' and p.indexname='ix_signal_snapshots_plan_latest'",
 'tasks': "select kind,state,count(*) n,sum(coalesce((payload->>'completed_count')::int,0)) completed,sum(coalesce((payload->>'failed_count')::int,0)) failed,max((payload->>'finished_at')::bigint) latest_finish,max(:at-(payload->>'submitted_at')::bigint) filter(where state='queued') oldest_pending_ms from tasks where kind like 'p39_%' group by 1,2 order by 1,2",
 'projection': "select count(*) n,max(computed_at) latest_projection,count(*) filter(where action in ('trial','participate') and valid_until>:at) unexpired_buy from current_signals",
 'beats': "select role,state,updated_at,details from worker_heartbeats where details->>'run_id'=:run and (role in ('analysis_owner','signal_projection','anomaly_discovery','api','jobs','live','streams') or role like 'stream_binance%')",
 'source': "select i.venue,count(*) markets,count(*) filter(where :at-m.as_of between 0 and 15000) quotes_15s,count(*) filter(where :at-(m.payload->>'oi_as_of')::bigint between 0 and 90000) oi_90s,count(*) filter(where :at-(m.payload->>'oi_as_of')::bigint between 0 and 360000) oi_6m from instruments i join market_states m on m.instrument_id=i.id where i.venue in ('binance','binance_spot') group by 1",
 'dia_snapshots': "select s.id,s.plan_id,s.as_of,p.expires_at,s.payload->'action' action,s.payload->'execution' execution,s.payload->'market' market from signal_snapshots s join plans p on p.id=s.plan_id where s.plan_id='p33-cff654d61f47d61a4bcc838a:p33' and s.as_of>=:start order by s.as_of desc limit 3",
 'recent_snapshots': "select id,plan_id,as_of,payload->>'action' action,payload->'execution' execution,payload->'market' market from signal_snapshots where as_of>=:start order by as_of desc limit 6",
 'structure_checks': "select key,updated_at,payload from user_preferences where key like 'p37%' order by updated_at desc limit 3",
}
start_ms = int(datetime.strptime(run[:15], '%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc).timestamp()*1000)
db = create_engine(settings().database_url, isolation_level='AUTOCOMMIT', connect_args={'options': '-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000'})
with db.connect() as connection:
    for key, query in queries.items():
        try:
            rows = [dict(r) for r in connection.execute(text(query), {'at': at, 'run': run, 'start': start_ms}).mappings()]
            if key == 'beats':
                for row in rows:
                    row['details'] = {k: v for k, v in row['details'].items() if k in ('run_id','pid','source_hashes','execution_owner','producer_ms','tasks','last_batch_ms','scanned','updated','write_queue')}
            if key == 'structure_checks':
                for row in rows:
                    row['payload'] = {k: v for k, v in row['payload'].items() if k in ('structure_checked_at','trend_checked_at','state','status','markets','active','slots')}
            sample['queries'][key] = rows
        except Exception as exc:
            sample['queries'][key] = {'error': type(exc).__name__}
db.dispose()
for process in processes:
    if process['run_id'] != run:
        continue
    log = Path(process['log_dir']) / (process['name'] + '.err.log')
    content = log.read_text('utf8', errors='replace') if log.exists() else ''
    patterns = {'traceback': 'Traceback', 'rate_429': r'\b429\b', 'ban_418': r'\b418\b',
                'timeout': 'TimeoutError|timed out', 'held_connection': 'database connection held', 'runtime_error': 'RuntimeError'}
    sample['logs'].append({'role': process['name'], 'path': str(log), 'bytes': log.stat().st_size if log.exists() else 0,
                           'patterns': {k: len(re.findall(v, content, re.I)) for k,v in patterns.items()},
                           'warning_lines': [line for line in content.splitlines() if re.search('database connection held|Traceback|TimeoutError|timed out|RuntimeError',line)][:20]})
path = out / ('remaining-activation-sample-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.json')
path.write_text(json.dumps(sample, ensure_ascii=False, indent=2, default=str), encoding='utf8')
def compact_snapshot(row):
    market = row.get('market') or {}
    return {k: row.get(k) for k in ('id','plan_id','as_of','expires_at','action','execution')} | {'market': {k: market.get(k) for k in ('instrument_id','price','as_of','quote_usd_rate')}}
print(json.dumps({'path': str(path), 'ports': sample['ports'], 'http': {k: {f:v for f,v in item.items() if f != 'fields'} for k,item in sample['http'].items()}, 'source_manifest_all_match': all(sample['source_manifest_matches'].values()), 'tasks': sample['queries']['tasks'], 'projection': sample['queries']['projection'], 'source': sample['queries']['source'], 'dia_snapshots': [compact_snapshot(r) for r in sample['queries']['dia_snapshots']], 'logs': sample['logs']}, ensure_ascii=False, default=str))
