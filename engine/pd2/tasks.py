"""Durable leases and diagnostic retry state shared by worker roles."""
from hashlib import sha256
from uuid import uuid4
from sqlalchemy import select,text
from engine.db import Session
from engine.models import Task
from engine.adapters.base import now_ms

def claim(kind,subject,ttl_ms=120000):
    key=kind+':'+subject;ident=sha256(key.encode()).hexdigest();owner=uuid4().hex;now=now_ms()
    with Session.begin() as s:
        if s.bind.dialect.name=='postgresql':s.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':ident})
        row=s.get(Task,ident)
        if row and row.state=='leased' and (row.lease_until or 0)>now:return None
        if not row:
            row=Task(id=ident,dedupe_key=ident,kind=kind,state='leased',available_at=now,attempts=0,payload={'subject':subject});s.add(row)
        row.state='leased';row.lease_until=now+ttl_ms;row.lease_owner=owner;row.attempts+=1
        return {'id':ident,'owner':owner}

def finish(lease,error=None):
    if not lease:return
    with Session.begin() as s:
        row=s.get(Task,lease['id'])
        if row and row.lease_owner==lease['owner']:
            row.state='failed' if error else 'complete';row.lease_until=None;row.lease_owner=None
            row.payload={**row.payload,'finished_at':now_ms(),'error':error}
