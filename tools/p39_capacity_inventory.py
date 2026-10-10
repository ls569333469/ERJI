"""Read-only sizing/reference inventory. No DELETE, VACUUM or retention policy."""
from sqlalchemy import text

def inventory(connection):
    sizes=[dict(r) for r in connection.execute(text("SELECT relname,pg_total_relation_size(relid) AS bytes,n_live_tup AS estimated_rows FROM pg_stat_user_tables ORDER BY bytes DESC")).mappings()]
    refs=[dict(r) for r in connection.execute(text("SELECT conrelid::regclass::text AS dependent,pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE contype='f' AND confrelid='raw_events'::regclass")).mappings()]
    return {'tables':sizes,'raw_references':refs,'raw_retention':'pending_user_decision','old_data_modified':False,'growth_measurement':'take two timestamped inventories at least one hour apart; bytes delta is physical growth, not exact inserted bytes','coordination_bounds':{'windows':256,'fact_queue':256,'fact_batch':32,'analysis_concurrency':4,'deep_slots':50,'research_plan_batch':20}}

if __name__=='__main__':
    from tools.p34_local import prepare
    prepare()
    from engine.db import engine
    import json
    from pathlib import Path
    from datetime import datetime,timezone
    with engine.connect() as c:
        c.execute(text('SET TRANSACTION READ ONLY'));report=inventory(c)
    report['captured_at']=datetime.now(timezone.utc).isoformat()
    output=Path('docs/p39-validation/capacity-inventories.jsonl')
    with output.open('a',encoding='utf8') as f:f.write(json.dumps(report,ensure_ascii=False)+'\n')
    print(json.dumps({'tables':len(report['tables']),'retention':report['raw_retention'],'output':str(output)}))
