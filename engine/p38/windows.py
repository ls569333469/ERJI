"""Rebuildable cache of committed rows, keyed by the full revision aggregate."""
from collections import OrderedDict
from copy import deepcopy
from threading import RLock
from sqlalchemy import select,func,values,column,String,Integer,true
from engine.models import Candle

_cache=OrderedDict();_lock=RLock();MAX_WINDOWS=256

def batch_tokens(s,ids,frames,now,limits=None):
    """One bounded indexed window per market/frame, without lifetime aggregates."""
    if not ids:return {}
    limits=limits or {}
    targets=values(column('iid',String),column('tf',String),column('n',Integer),name='window_targets').data(
        [(iid,tf,limits.get(tf,400)) for iid in sorted(set(ids)) for tf in frames])
    recent=select(Candle.open_time,Candle.received_at,Candle.revision).where(
        Candle.instrument_id==targets.c.iid,Candle.resolution==targets.c.tf,
        Candle.is_closed.is_(True),Candle.close_time<=now,Candle.received_at<=now
    ).order_by(Candle.open_time.desc()).limit(targets.c.n).lateral('recent_window')
    result={iid:{} for iid in ids}
    for iid,tf,count,received,revision,opened in s.execute(select(targets.c.iid,targets.c.tf,
        func.count(recent.c.open_time),func.max(recent.c.received_at),func.sum(recent.c.revision),func.max(recent.c.open_time)
    ).select_from(targets.outerjoin(recent,true())).group_by(targets.c.iid,targets.c.tf)):
        result[iid][tf]=(count,received,revision,opened)
    return {iid:tuple((tf,result[iid][tf]) for tf in frames) for iid in ids}

def token(s,iid,tf,now,closed_only=True,limit=400):
    filters=[Candle.instrument_id==iid,Candle.resolution==tf,Candle.received_at<=now]
    if closed_only:filters.extend([Candle.is_closed.is_(True),Candle.close_time<=now])
    recent=select(Candle.received_at,Candle.revision,Candle.open_time).where(*filters).order_by(Candle.open_time.desc()).limit(limit).subquery()
    return tuple(s.execute(select(func.count(),func.max(recent.c.received_at),func.sum(recent.c.revision),func.max(recent.c.open_time))).one())

def read(s,iid,tf,now,limit=300,closed_only=True):
    version=token(s,iid,tf,now,closed_only,limit)
    # A connection-scoped test transaction must not share an uncommitted cache.
    database=str(s.bind.url) if hasattr(s.bind,'url') else str(id(s.bind))
    key=(database,iid,tf,limit,closed_only)
    with _lock:
        old=_cache.get(key)
        if old and old[0]==version:_cache.move_to_end(key);return deepcopy(old[1])
    filters=[Candle.instrument_id==iid,Candle.resolution==tf,Candle.received_at<=now]
    if closed_only:filters.extend([Candle.is_closed.is_(True),Candle.close_time<=now])
    records=list(s.scalars(select(Candle).where(*filters).order_by(Candle.open_time.desc()).limit(limit)))
    rows=[{k:getattr(c,k) for k in ('open_time','close_time','received_at','is_closed','open','high','low','close','base_volume','quote_volume','revision','raw_ref')} for c in reversed(records)]
    with _lock:
        _cache[key]=(version,rows);_cache.move_to_end(key)
        while len(_cache)>MAX_WINDOWS:_cache.popitem(last=False)
    return deepcopy(rows)
