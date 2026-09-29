"""Export an explicit identity review table; never rewrite native IDs/history."""
import csv,json
from pathlib import Path
from sqlalchemy import select
from engine.db import Session
from engine.models import Instrument,IdentityLink
from engine.pd2.universe_policy import membership,primary_eligible,PRIMARY

with Session() as session:
    aliases,admitted=membership(session)
    links={r.asset_id:r for r in session.scalars(select(IdentityLink).where(IdentityLink.provider=='cmc',IdentityLink.status=='verified'))}
    rows=[]
    for i in session.scalars(select(Instrument).order_by(Instrument.id)):
        canonical=aliases.get(i.asset_id,i.asset_id);link=links.get(i.asset_id)
        rows.append({'instrument_id':i.id,'original_asset_id':i.asset_id,'canonical_asset_id':canonical,
            'source':i.venue,'primary_eligible':primary_eligible(i),'matched_primary_asset':canonical in admitted,
            'identity_evidence':json.dumps(link.evidence,ensure_ascii=False) if link else 'unverified_cross_source_identity',
            'unit_policy':i.spec.get('unit_policy','native_unit; do not strip numeric prefixes'),
            'history_policy':'retain_native_id_and_all_plans'})
path=Path('docs/26_identity_review_local.csv')
with path.open('w',encoding='utf-8-sig',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
print(json.dumps({'scope':'local_probe_catalog_not_production','market_rows':len(rows),'unique_admitted_assets':len(admitted),'aliases_changed':sum(r['original_asset_id']!=r['canonical_asset_id'] for r in rows),'unmatched_auxiliary_markets':sum(r['source'] not in PRIMARY and not r['matched_primary_asset'] for r in rows),'output':str(path)},ensure_ascii=False))
