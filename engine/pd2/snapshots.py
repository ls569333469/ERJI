"""Deduplicate immutable chart inputs without rewriting old decisions."""
from hashlib import sha256
from copy import deepcopy
import json
from engine.models import SnapshotDataset
from engine.adapters.base import now_ms

def freeze_datasets(session,snapshot):
    payload=deepcopy(snapshot);datasets={}
    for field in ('candles_at_snapshot','derivative_history_at_snapshot'):
        if field in payload:datasets[field]=payload.pop(field)
    datasets['indicator_series']={a['timeframe']:a.pop('series',[]) for a in payload.get('timeframe_assessments',[])}
    references={}
    for field,data in datasets.items():
        digest=sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if not session.get(SnapshotDataset,digest):session.add(SnapshotDataset(id=digest,created_at=now_ms(),payload={'value':data}))
        references[field]=digest
    payload['datasets']=references
    return payload

def hydrate(session,snapshot):
    payload=deepcopy(snapshot)
    for field,digest in payload.get('datasets',{}).items():
        row=session.get(SnapshotDataset,digest)
        if not row:continue
        data=row.payload['value']
        if field=='indicator_series':
            for assessment in payload.get('timeframe_assessments',[]):assessment['series']=data.get(assessment['timeframe'],[])
        else:payload[field]=data
    return payload
