"""Immutable long-history indicator pages prepared outside HTTP requests."""
import json
from hashlib import sha256
from sqlalchemy import select,func
from engine.models import Candle,SnapshotDataset,UserPreference

VERSION='offline-indicators-v1'
PAGE=1000


def digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def source_state(session,instrument,resolution,now):
    row=session.execute(select(func.count(),func.min(Candle.open_time),func.max(Candle.close_time),
        func.max(Candle.received_at),func.sum(Candle.revision)).where(Candle.instrument_id==instrument,
        Candle.resolution==resolution,Candle.is_closed.is_(True),Candle.close_time<=now,Candle.received_at<=now)).one()
    return dict(zip(('count','start','end','received_at','revision_sum'),row))


def key(instrument,resolution):
    return 'p33_indicator_offline:'+digest([instrument,resolution])


def build(session,instrument,resolution,now,max_rows=250000):
    from engine.pd2.service import candle_dict
    from engine.pd2.indicator_history import calculate
    initial=source_state(session,instrument,resolution,now)
    if not initial['count']:return {'status':'missing_data'}
    if initial['count']>max_rows:return {'status':'offline_budget_limit','count':initial['count'],'limit':max_rows}
    previous=session.get(UserPreference,key(instrument,resolution))
    if previous and previous.payload.get('source')==initial and previous.payload.get('version')==VERSION:
        return {'status':'unchanged','series_revision':previous.payload['series_revision'],'count':initial['count']}
    rows=[];cursor=-1
    while True:
        batch=list(session.scalars(select(Candle).where(Candle.instrument_id==instrument,Candle.resolution==resolution,
            Candle.open_time>cursor,Candle.open_time<initial['end'],Candle.is_closed.is_(True),
            Candle.close_time<=now,Candle.received_at<=now).order_by(Candle.open_time).limit(2000)))
        if not batch:break
        rows.extend(candle_dict(b) for b in batch);cursor=batch[-1].open_time
        if len(rows)>max_rows:return {'status':'source_changed_during_build'}
    if len(rows)!=initial['count'] or source_state(session,instrument,resolution,now)!=initial:
        return {'status':'source_changed_during_build'}
    # Full deterministic seed is computed only by this offline job, never by GET.
    encoded=json.dumps(rows,sort_keys=True,separators=(',',':'))
    revision=digest([VERSION,instrument,resolution,sha256(encoded.encode()).hexdigest()])
    series=calculate(encoded,resolution);segments=[]
    for start in range(0,len(series),PAGE):
        part=series[start:start+PAGE]
        payload={'kind':VERSION,'series_revision':revision,'instrument_id':instrument,'timeframe':resolution,'series':part}
        ident=digest(payload)
        if not session.get(SnapshotDataset,ident):session.add(SnapshotDataset(id=ident,created_at=now,payload=payload))
        segments.append({'id':ident,'start':part[0]['time'],'end':part[-1]['close_time'],'count':len(part)})
    manifest={'version':VERSION,'source':initial,'series_revision':revision,'segments':segments,'as_of':now}
    ident=digest(manifest)
    if not session.get(SnapshotDataset,ident):session.add(SnapshotDataset(id=ident,created_at=now,payload=manifest))
    if source_state(session,instrument,resolution,now)!=initial:return {'status':'source_changed_during_build'}
    session.merge(UserPreference(key=key(instrument,resolution),updated_at=now,payload={**manifest,'manifest_id':ident}))
    return {'status':'built','series_revision':revision,'count':len(series),'segments':len(segments)}


def read(session,instrument,resolution,before,limit,now,source):
    saved=session.get(UserPreference,key(instrument,resolution))
    meta=saved.payload if saved else {}
    base={'instrument_id':instrument,'timeframe':resolution,'as_of':now,'snapshot_id':None,
          'rows':[],'has_more':False,'series_revision':meta.get('series_revision','offline_missing')}
    if meta.get('version')!=VERSION or meta.get('source')!=source:
        return {**base,'status':'offline_rebuild_required','reason':'长历史指标版本待后台重建；当前数据已变化或尚未建立离线版本'}
    rows=[]
    for segment in reversed(meta['segments']):
        if before is not None and segment['start']>=before:continue
        data=session.get(SnapshotDataset,segment['id'])
        if not data or data.payload.get('series_revision')!=meta['series_revision']:
            return {**base,'status':'offline_segment_missing','reason':'离线指标分段缺失，暂停该版本显示'}
        part=[p for p in data.payload['series'] if before is None or p['time']<before]
        rows=part+rows
        if len(rows)>=limit:break
    rows=rows[-limit:]
    return {**base,'rows':rows,'status':'available','reason':None,
            'coverage_start':source['start'],'coverage_end':source['end'],
            'has_more':bool(rows and rows[0]['time']>source['start']),
            'next_before':rows[0]['time'] if rows else None,'method':VERSION}


def refresh_one(now):
    """One market per existing five-minute coverage task; no external requests."""
    from engine.db import Session
    with Session.begin() as session:
        targets=list(session.execute(select(Candle.instrument_id,Candle.resolution).where(
            Candle.is_closed.is_(True),Candle.close_time<=now,Candle.received_at<=now,
            Candle.resolution.in_(['60','240','1D','1W'])).group_by(Candle.instrument_id,Candle.resolution)
            .having(func.count()>20000).order_by(Candle.instrument_id,Candle.resolution)))
        saved=session.get(UserPreference,'p33_offline_indicator_cursor')
        previous=tuple((saved.payload if saved else {}).get('last',[]))
        selected=next((tuple(r) for r in targets if tuple(r)>previous),tuple(targets[0]) if targets else None)
        result=build(session,*selected,now) if selected else {'status':'no_long_history_markets'}
        session.merge(UserPreference(key='p33_offline_indicator_cursor',updated_at=now,
            payload={'last':list(selected) if selected else [],'eligible_markets':len(targets),'result':result,
                     'max_markets_per_run':1,'external_requests':0}))
        return result
