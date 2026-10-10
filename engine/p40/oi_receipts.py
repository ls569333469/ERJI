"""Bounded diagnostic comparison; never feeds points into the detector."""
import time
from sqlalchemy import select,values,column,String,true
from engine.models import OiPoint

def compare_points(rows,memory,evaluated_until,at):
    result=[]
    for iid,source,quantity,known in rows:
        present=source in memory.get(iid,set())
        result.append({'instrument_id':iid,'source_at':source,'known_at':known,
            'quantity_available':quantity is not None,'in_detector_memory':present,
            'known_before_last_evaluation':known is not None and known<=evaluated_until.get(iid,0),
            'source_age_ms':at-source,'scope':'sampled persisted endpoint; absence is not proof of lost signal'})
    return result

def sample_persisted(source,offset=0,clock=None):
    from engine.db import Session
    at=clock() if clock else int(time.time()*1000)
    with source.lock:
        ids=sorted(iid for iid,m in source.catalog.items() if m.get('venue')=='binance')
        selected=ids[offset:offset+30]
        memory={iid:{stamp for stamp,_ in source.oi.get(iid,())} for iid in selected}
        until={iid:source.oi_evaluations[iid][-1]['evaluated_until'] for iid in selected if source.oi_evaluations.get(iid)}
    rows=[]
    if selected:
        demand=values(column('iid',String),name='oi_receipt_probe').data([(iid,) for iid in selected])
        recent=select(OiPoint.event_time,OiPoint.base_quantity,OiPoint.received_at).where(
            OiPoint.instrument_id==demand.c.iid,OiPoint.event_time>=at-360000,OiPoint.event_time<=at,
            OiPoint.received_at<=at).order_by(OiPoint.event_time.desc()).limit(8).lateral()
        with Session() as s:rows=list(s.execute(select(demand.c.iid,recent).select_from(demand.join(recent,true()))))
    return {'snapshot_at':at,'scope':'rotating <=30 native markets, <=8 endpoints each, past 6m; once per ordinary cycle; no ingestion/cursor changes',
        'sampled_instruments':selected,'endpoints':compare_points(rows,memory,until,at),
        'next_offset':offset+len(selected) if offset+len(selected)<len(ids) else 0}
