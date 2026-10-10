"""Publish only at the transaction tail, after business writes and fences."""
from copy import deepcopy
from dataclasses import dataclass
from sqlalchemy import select,text

@dataclass
class EventReceipt:
    event: object

    @property
    def id(self):return self.event.id

def queue_event(session,kind,created_at,payload):
    from engine.models import StreamEvent
    receipt=EventReceipt(StreamEvent(kind=kind,created_at=created_at,payload=deepcopy(payload)))
    session.info.setdefault('transaction_events',[]).append(receipt)
    return receipt

def add_outbox(session,row,receipt):
    """Prepare notification rows before publication without requiring an ID."""
    if isinstance(receipt,EventReceipt):
        if receipt not in session.info.get('transaction_events',[]):raise ValueError('event belongs to another transaction')
        session.info.setdefault('transaction_outboxes',[]).append((row,receipt))
    else:
        row.event_id=receipt;session.add(row)

def publish(session):
    receipts=session.info.get('transaction_events',[])
    if not receipts:return
    from engine.models import Outbox
    # All ORM business changes and commit guards must precede the global lock.
    from engine.p40.database import measure
    with measure('publication_business_flush','events'):session.flush()
    drafts=[];seen=set()
    for row,receipt in sorted(session.info.get('transaction_outboxes',[]),key=lambda x:x[0].dedupe_key):
        if row.dedupe_key in seen:continue
        seen.add(row.dedupe_key)
        if session.bind.dialect.name=='postgresql':
            session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:k,0))'),{'k':'notification:'+row.dedupe_key})
        if not session.scalar(select(Outbox.id).where(Outbox.dedupe_key==row.dedupe_key)):
            drafts.append((row,receipt))
    session.flush()
    if session.bind.dialect.name=='postgresql':
        with measure('publication_lock_wait','events'):session.execute(text('SELECT pg_advisory_xact_lock(1877101)'))
    events=[r.event for r in receipts]
    session.add_all(events)
    with measure('publication_event_flush','events'):session.flush(events)
    # These are exclusively new rows, with their unique keys reserved above.
    # No shared business-row reads/updates occur under the publication lock.
    rows=[]
    for row,receipt in drafts:
        row.event_id=receipt.id;rows.append(row)
    if rows:session.add_all(rows);session.flush(rows)
    session.info.pop('transaction_events',None);session.info.pop('transaction_outboxes',None)

def discard(session):
    session.info.pop('transaction_events',None);session.info.pop('transaction_outboxes',None)
