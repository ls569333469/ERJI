"""Small safe provenance record; no env values, credentials or DB URL."""
from pathlib import Path
import hashlib,subprocess

def manifest(role):
    root=Path(__file__).resolve().parents[2]
    try:commit=subprocess.run(['git','rev-parse','HEAD'],cwd=root,capture_output=True,text=True,timeout=3).stdout.strip()
    except (OSError,subprocess.TimeoutExpired):commit='unknown'
    hashes={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in ('engine/p38/jobs.py','engine/p38/candle_gaps.py','engine/p38/observation_query.py','engine/pd2/source_policy.py','engine/pd2/binance_facts.py','engine/p37/worker.py','engine/p37/analysis.py','services/worker/main.py','services/api/main.py')}
    import os
    for p in ('engine/p39/pipeline.py','engine/p39/diagnostics.py','migrations/versions/p39_layered_analysis.py'):
        hashes[p]=hashlib.sha256((root/p).read_bytes()).hexdigest()
    for p in ('engine/p40/diagnostics.py','engine/p40/streams.py','engine/p40/database.py','engine/p40/receivers.py','engine/p38/fences.py','engine/pd2/native_streams.py','engine/repository.py','engine/p37/detector.py','engine/signal_service.py','engine/pd2/service.py','engine/transaction_events.py','engine/p37/store.py','engine/db.py','engine/pd2/collection_schedule.py','engine/pd2/p35_capacity.py','engine/p40/oi_receipts.py'):
        hashes[p]=hashlib.sha256((root/p).read_bytes()).hexdigest()
    return {'role':role,'run_id':os.getenv('ERJI_RUN_ID'),'pid':os.getpid(),'checkout':str(root),'commit':commit,'source_hashes':hashes,'config_sources':{'runtime':'settings_versions published','anomaly':'UserPreference:p37_config','quota':'p36_budget constants + checkpoint','startup':'environment/shared .env/checkout .env'},'code_generation':'P40-reliability-r4'}
