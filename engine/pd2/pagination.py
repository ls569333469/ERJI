"""Stable ordering per list snapshot; safety status is always read from latest decisions."""
from hashlib import sha256
from uuid import uuid4
from decimal import Decimal as D
from sqlalchemy import select
from engine.db import Session
from engine.models import DataCapability
from engine.adapters.base import now_ms

def paginate(result,view,query,sort,page,size,snapshot_id=None):
    now=now_ms();query=query.strip().lower();signature=sha256((view+'|'+query+'|'+sort).encode()).hexdigest()
    rows=[r for r in result['rows'] if query in (r['asset_id']+' '+r['market'].get('symbol','')).lower()]
    current={r['asset_id']:r for r in rows};order=None
    with Session.begin() as s:
        old=s.get(DataCapability,'erji:list:'+str(snapshot_id)) if snapshot_id else None
        if old and old.expires_at>now and old.payload.get('signature')==signature:order=old.payload['asset_ids']
        if order is None:
            def numeric(r):
                if sort=='oi':value=r['market'].get('oi_notional_usd')
                else:value=((r.get('opportunity_plan') or {}).get('t1') or {}).get('remaining_upside_pct')
                return D(str(value)) if value is not None else D('-Infinity')
            rows.sort(key=lambda r:(-numeric(r) if sort!='first' else -r['first_seen_at'],r['asset_id'],r['id']))
            order=[r['asset_id'] for r in rows];snapshot_id=uuid4().hex
            s.add(DataCapability(key='erji:list:'+snapshot_id,provider='erji',capability='list_snapshot',subject=snapshot_id,status='fresh',received_at=now,observed_at=now,expires_at=now+86400000,payload={'signature':signature,'asset_ids':order}))
    cohort=[current[a] for a in order if a in current];counts={};numbered=[]
    for i,r in enumerate(cohort):
        counts[r['phase']]=counts.get(r['phase'],0)+1
        numbered.append({**r,'row_number':i+1,'group_row_number':counts[r['phase']]})
    pages=max(1,(len(cohort)+size-1)//size);page=min(page,pages)
    groups=[{**g,'asset_count':counts.get(g['phase_id'],0)} for g in result['groups']]
    return {**result,'rows':numbered[(page-1)*size:page*size],'groups':groups,'pagination':{'list_snapshot':snapshot_id,'page':page,'page_size':size,'pages':pages,'total':len(cohort),'new_assets_waiting':len(set(current)-set(order)),'ordering':'fixed_until_manual_refresh','safety':'current_actions_not_frozen'}}
