"""Event-owned cumulative AVWAP, advanced in bounded database-only pages."""
from decimal import Decimal as D, InvalidOperation
from hashlib import sha256
import json
from sqlalchemy import select
from engine.models import Candle, UserPreference, SnapshotDataset
from engine.pd2.analysis import DURATIONS

VERSION = 'persistent-event-avwap-v2'
PAGE_LIMIT = 1400


def restart(session, previous, anchor, timeframe, now, reason):
    """Archive the old generation; no frozen dataset or snapshot is rewritten."""
    payload={'kind':'avwap_superseded_generation','state':previous,'superseded_at':now,'reason':reason}
    identity=sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    if not session.get(SnapshotDataset,identity):
        session.add(SnapshotDataset(id=identity,created_at=now,payload=payload))
    return {'anchor_time':anchor,'timeframe':timeframe,'next_open':anchor,'volume':'0','weighted':'0',
            'fingerprints':{},'segments':[],'status':'rebuilding','rebuild_target':previous['next_open'],
            'generation':previous.get('generation',1)+1,'supersedes':identity,'rebuild_reason':reason,
            'audit_cursor':0}


def audit_segment(session, previous, instrument, timeframe, now):
    """Rotate one immutable page per call, including data older than 400 bars."""
    segments=previous.get('segments',[])
    if not segments:return None,0
    position=previous.get('audit_cursor',0)%len(segments)
    segment=segments[position]
    identity=segment['id'] if isinstance(segment,dict) else segment
    dataset=session.get(SnapshotDataset,identity)
    expected=dataset.payload.get('source_fingerprints') if dataset else None
    if not expected:return 'legacy_segment_without_revision_proof',0
    start,end=min(map(int,expected)),max(map(int,expected))+DURATIONS[timeframe]
    from engine.pd2.service import candle_dict
    rows=session.scalars(select(Candle).where(Candle.instrument_id==instrument,Candle.resolution==timeframe,
        Candle.open_time>=start,Candle.open_time<end,Candle.is_closed.is_(True),
        Candle.received_at<=now,Candle.close_time<=now).order_by(Candle.open_time).limit(PAGE_LIMIT+1))
    actual={str(b.open_time):fingerprint(candle_dict(b)) for b in rows}
    return (None if actual==expected else 'historical_segment_revised_or_missing'),(position+1)%len(segments)


def fingerprint(bar):
    return sha256(json.dumps({k: bar.get(k) for k in ('open_time','close_time','high','low','close','base_volume','revision')}, sort_keys=True).encode()).hexdigest()


def accumulate(previous, rows, anchor, timeframe, now):
    """A corrected processed candle suspends the version; never silently reanchor."""
    state = dict(previous or {'anchor_time': anchor, 'timeframe': timeframe, 'next_open': anchor,
                             'volume': '0', 'weighted': '0', 'fingerprints': {}, 'status': 'building'})
    state['fingerprints'] = dict(state['fingerprints'])
    points = []
    if state['status'] == 'revision_conflict':
        return state, points
    weighted, volume = D(state['weighted']), D(state['volume'])
    for bar in sorted(rows, key=lambda b: b['open_time']):
        if not bar['is_closed'] or max(bar['close_time'], bar.get('received_at', 0)) > now:
            continue
        at = bar['open_time']
        old = state['fingerprints'].get(str(at))
        if old and old != fingerprint(bar):
            state.update(status='revision_conflict', blocked_at=at)
            return state, []
        if at < state['next_open']:
            continue
        if at != state['next_open'] or bar['close_time'] != at + DURATIONS[timeframe]:
            state.update(status='interval_gap', blocked_at=state['next_open'])
            break
        try:
            q = D(str(bar.get('base_volume')))
            p = (D(bar['high']) + D(bar['low']) + D(bar['close'])) / 3
            if not q.is_finite() or q < 0 or not p.is_finite() or p <= 0:
                raise ValueError('volume')
        except (InvalidOperation, TypeError, ValueError):
            state.update(status='missing_volume', blocked_at=at)
            break
        volume += q
        weighted += p*q
        state['fingerprints'][str(at)] = fingerprint(bar)
        state['next_open'] = bar['close_time']
        points.append({'time': at, 'known_at': max(bar['close_time'], bar.get('received_at', 0)),
                       'event_avwap': str(weighted/volume) if volume > 0 else None})
        state['status'] = 'available' if volume > 0 else 'zero_volume'
        state.pop('blocked_at', None)
    state.update(volume=str(volume), weighted=str(weighted))
    state['fingerprints'] = dict(sorted(state['fingerprints'].items(), key=lambda p: int(p[0]))[-400:])
    return state, points


def update(session, event_id, instrument, timeframe, anchor, now):
    key = 'p33_avwap:' + sha256((event_id+instrument+timeframe+str(anchor)).encode()).hexdigest()
    saved = session.get(UserPreference, key)
    previous = saved.payload if saved else {}
    cursor = previous.get('next_open', anchor)
    if previous and now-previous.get('as_of',0)<60000 and now<cursor+DURATIONS[timeframe]:
        return previous
    audit_cursor=previous.get('audit_cursor',0)
    if previous:
        reason=None
        if previous.get('version')!=VERSION:reason='upgrade_requires_full_revision_proof'
        elif previous.get('status')=='revision_conflict':reason='recent_source_revision'
        else:reason,audit_cursor=audit_segment(session,previous,instrument,timeframe,now)
        if reason:
            previous=restart(session,previous,anchor,timeframe,now,reason)
            cursor=anchor;audit_cursor=0
    start = max(anchor, cursor - 400 * DURATIONS[timeframe])
    rows = session.scalars(select(Candle).where(Candle.instrument_id == instrument,
        Candle.resolution == timeframe, Candle.open_time >= start, Candle.received_at <= now,
        Candle.close_time <= now, Candle.is_closed.is_(True)).order_by(Candle.open_time).limit(PAGE_LIMIT))
    from engine.pd2.service import candle_dict
    source=[candle_dict(b) for b in rows]
    state, points = accumulate(previous or None, source, anchor, timeframe, now)
    if state['status']=='revision_conflict':
        state=restart(session,previous,anchor,timeframe,now,'recent_source_revision')
        previous=state;points=[];audit_cursor=0
    segments = list(previous.get('segments', []))
    if points:
        point_times={p['time'] for p in points}
        payload = {'event_id': event_id, 'instrument_id': instrument, 'timeframe': timeframe,
                   'anchor_time': anchor, 'generation':state.get('generation',1),'series': points,
                   'source_fingerprints':{str(b['open_time']):fingerprint(b) for b in source if b['open_time'] in point_times}}
        identity = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if not session.get(SnapshotDataset, identity):
            session.add(SnapshotDataset(id=identity, created_at=now, payload=payload))
        if not any((p.get('id') if isinstance(p,dict) else p)==identity for p in segments):
            segments.append({'id':identity,'start':points[0]['time'],'end':points[-1]['time']+DURATIONS[timeframe]})
    if state.get('rebuild_target'):
        if state['next_open']<state['rebuild_target']:
            if state['status'] in ('available','zero_volume'):state['status']='rebuilding'
        else:state.pop('rebuild_target')
    state.update(segments=segments, as_of=now, method='HLC3_base_volume', version=VERSION,
                 generation=state.get('generation',1),audit_cursor=audit_cursor,
                 audit_checked_at=now,audit_method='one_archived_segment_per_update')
    session.merge(UserPreference(key=key, updated_at=now, payload=state))
    return state


def read_series(session, state, start=None, end=None):
    if state.get('status') in ('revision_conflict','rebuilding') or state.get('rebuild_target'): return []
    points = []
    for segment in state.get('segments', []):
        if isinstance(segment,dict):
            if start is not None and segment['end']<=start or end is not None and segment['start']>=end:continue
            identity=segment['id']
        else:identity=segment
        dataset = session.get(SnapshotDataset, identity)
        if dataset:
            points.extend(p for p in dataset.payload['series'] if
                          (start is None or p['time'] >= start) and (end is None or p['time'] < end))
    return points
