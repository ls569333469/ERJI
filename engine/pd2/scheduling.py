"""Persistent oldest-first rotation with active-plan and watchlist priority."""
from sqlalchemy import select,func,text
from engine.db import Session,engine
from engine.models import Candle,Campaign,Episode,Watchlist,Instrument,UserPreference
from engine.adapters.base import now_ms

def rotation(venue,natives,limit):
    with Session.begin() as s:
        key="scheduler:"+venue
        checkpoint=s.get(UserPreference,key)
        seen=(checkpoint.payload if checkpoint else {}).get("visited",{})
        active=set(s.scalars(select(Instrument.native_symbol).join(Campaign,Campaign.reference_instrument_id==Instrument.id).join(Episode).where(Instrument.venue==venue,Episode.phase.notin_(["ended","invalidated"]))))
        watched=set(s.scalars(select(Instrument.native_symbol).join(Watchlist,Watchlist.asset_id==Instrument.asset_id).where(Instrument.venue==venue)))
        priority=sorted(set(natives)&(active|watched),key=lambda k:(seen.get(k,0),k))
        background=sorted(set(natives)-set(priority),key=lambda k:(seen.get(k,0),k))
        # At least one background slot on every round; activity cannot starve the catalog.
        selected=priority[:max(1,limit-1)]+background[:max(1,limit-len(priority[:max(1,limit-1)]))]
        for native in selected:seen[native]=now_ms()
        s.merge(UserPreference(key=key,payload={"visited":seen,"catalog_count":len(natives)},updated_at=now_ms()))
        return selected

def role_lock(role):
    connection=engine.connect()
    key=connection.execute(text("SELECT hashtext(current_schema() || :role)"),{"role":":worker:"+role}).scalar()
    if not connection.execute(text("SELECT pg_try_advisory_lock(:key)"),{"key":key}).scalar():
        connection.close();raise RuntimeError("duplicate_worker_role")
    connection.commit()
    return connection
