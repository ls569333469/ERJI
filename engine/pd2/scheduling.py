"""Persistent oldest-first rotation with active-plan and watchlist priority."""
from sqlalchemy import select,func,text
from engine.db import Session,engine
from engine.models import Candle,Campaign,Episode,Watchlist,Instrument,UserPreference
from engine.adapters.base import now_ms

def rotation(venue,natives,limit):
    with Session.begin() as s:
        key="scheduler:"+venue
        checkpoint=s.get(UserPreference,key)
        s.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':key})
        # Legacy JSON is a read-only migration fallback. Each native now owns
        # a small row, so one visit does not rewrite the whole catalog ledger.
        seen=dict((checkpoint.payload if checkpoint else {}).get("visited",{}))
        prefix=key+':native:'
        for row in s.scalars(select(UserPreference).where(UserPreference.key.startswith(prefix,autoescape=True))):seen[row.key[len(prefix):]]=row.updated_at
        active=set(s.scalars(select(Instrument.native_symbol).join(Campaign,Campaign.reference_instrument_id==Instrument.id).join(Episode).where(Instrument.venue==venue,Episode.phase.notin_(["ended","invalidated"]))))
        watched=set(s.scalars(select(Instrument.native_symbol).join(Watchlist,Watchlist.asset_id==Instrument.asset_id).where(Instrument.venue==venue)))
        priority=sorted(set(natives)&(active|watched),key=lambda k:(seen.get(k,0),k))
        background=sorted(set(natives)-set(priority),key=lambda k:(seen.get(k,0),k))
        # At least one background slot on every round; activity cannot starve the catalog.
        selected=priority[:max(1,limit-1)]+background[:max(1,limit-len(priority[:max(1,limit-1)]))]
        for native in selected:s.merge(UserPreference(key=prefix+native,payload={'native':native},updated_at=now_ms()))
        s.merge(UserPreference(key=key+':summary',payload={'catalog_count':len(natives),'coordination_format':'per_native_rows'},updated_at=now_ms()))
        return selected

def role_lock(role):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    from engine.config import settings
    # Role ownership is lifetime-long; never reserve a pooled query connection.
    lease_engine=create_engine(settings().database_url,poolclass=NullPool)
    connection=lease_engine.connect()
    key=connection.execute(text("SELECT hashtext(current_database() || :role)"),{"role":":worker:"+role}).scalar()
    gate=connection.execute(text("SELECT hashtext(current_database() || :role)"),{"role":":commit:"+role}).scalar()
    connection.execute(text('SELECT pg_advisory_lock(:key)'),{'key':gate})
    try:
        if not connection.execute(text("SELECT pg_try_advisory_lock(:key)"),{"key":key}).scalar():
            raise RuntimeError("duplicate_worker_role")
        pid=connection.execute(text('SELECT pg_backend_pid()')).scalar()
    except BaseException:
        connection.close();raise
    finally:
        if not connection.closed:connection.execute(text('SELECT pg_advisory_unlock(:key)'),{'key':gate})
    connection.commit()
    return RoleOwner(connection,key,gate,pid)

class RoleOwner:
    def __init__(self,connection,key,gate,pid):
        import threading
        self.connection=connection;self.key=key;self.gate=gate;self.pid=pid;self.mutex=threading.Lock();self.lost=False
    def guard(self,session):
        session.execute(text('SELECT pg_advisory_xact_lock_shared(:key)'),{'key':self.gate})
        self.check()
    def check(self):
        with self.mutex:
            if self.lost:raise RuntimeError('worker_role_lost')
            try:
                current=self.connection.execute(text('SELECT pg_backend_pid()')).scalar()
                held=self.connection.execute(text("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=:pid AND locktype='advisory' AND granted AND objid=:key AND classid=:high)"),{'pid':self.pid,'key':self.key & 0xffffffff,'high':(self.key >> 32)&0xffffffff}).scalar()
                self.connection.commit()
                if current!=self.pid or not held:raise RuntimeError('worker_role_lost')
            except BaseException:
                self.lost=True;raise
    def close(self):
        with self.mutex:self.lost=True;self.connection.close()
