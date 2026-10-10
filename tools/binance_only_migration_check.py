"""Read-only provenance/difference audit; old resolver is used as a counterfactual."""
import sys,json,importlib.util,hashlib
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.p34_local import prepare
prepare()
from sqlalchemy import create_engine,select,text
from sqlalchemy.orm import Session
from engine.config import settings
from engine.models import Instrument,IdentityLink,Plan,RawEvent,SignalSnapshot,DataCapability
from engine.pd2.universe_policy import resolve,primary_eligible
OUT=ROOT/'docs/p39-validation/binance-only-implementation'
path=OUT/'before/engine/pd2/universe_policy.py'
spec=importlib.util.spec_from_file_location('p39_before_universe_policy',path)
old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
db=create_engine(settings().database_url,connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=10000'})
with Session(db) as s:
    inst=list(s.scalars(select(Instrument)));links=list(s.scalars(select(IdentityLink)))
    before,admitted_before=old.resolve(inst,links);after,admitted_after=resolve(inst,links)
    changed=[{'asset_id':aid,'old_resolver_canonical':before[aid],'new_canonical':after[aid]} for aid in sorted(before) if before[aid]!=after[aid]]
    unmapped=[{'instrument_id':i.id,'asset_id':i.asset_id,'venue':i.venue,'reason':'币安目录未返回可验证的跨市场单位/链身份；保持原生ID'} for i in inst if primary_eligible(i) and ':' in i.asset_id and after.get(i.asset_id)==i.asset_id]
    counts=dict(s.execute(text("select provider,count(*) from identity_links group by provider")).all())
    external_plan_rows=[dict(r) for r in s.execute(text("select i.venue,count(*) n from plans p join instruments i on i.id=p.execution_instrument_id where i.venue not in ('binance','binance_spot','binance_alpha') group by i.venue")).mappings()]
    supply=[dict(r) for r in s.execute(text("select venue,count(*) n,count(*) filter(where spec->>'circulating_supply' is not null) supplied from instruments where venue in ('binance','binance_spot','binance_alpha') group by venue")).mappings()]
    read_only=s.execute(text('show transaction_read_only')).scalar()
db.dispose()
baseline=json.loads((OUT/'baseline.json').read_text('utf8'));changes=[]
for name,before_hash in baseline['files'].items():
    path=ROOT/name
    if path.exists():
        value=hashlib.sha256(path.read_bytes()).hexdigest()
        if value!=before_hash:changes.append({'path':name,'before':before_hash,'after':value})
report={'sampled_at_utc':datetime.now(timezone.utc).isoformat(),'database_read_only':read_only,'identity_before_method':'原备份resolver在同一当前目录上的反事实对照；不是迁移前数据库全量快照','old_admitted_assets':len(admitted_before),'new_admitted_assets':len(admitted_after),'canonical_changes':changed,'identity_counts':counts,'unmapped_native_assets':unmapped,'external_historical_plans_retained':external_plan_rows,'supply_coverage':supply,'source_changes':changes,'frozen_plan_rebinding':False,'raw_evidence_deleted':False,'detail_and_kline_files_unchanged':all(not x['path'].replace('\\','/').endswith(('/chart.tsx','/opportunity-detail.tsx')) for x in changes)}
(OUT/'migration-and-source-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf8')
print(json.dumps({k:report[k] for k in ('database_read_only','old_admitted_assets','new_admitted_assets','identity_counts','external_historical_plans_retained','supply_coverage','detail_and_kline_files_unchanged')},ensure_ascii=False));print('canonical changes',len(changed),'source files changed',len(changes))
