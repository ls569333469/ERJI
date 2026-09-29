"""Reclassify previously ingested non-crypto instruments; preserve all history."""
import pd2_env
from sqlalchemy import select
from engine.db import Session
from engine.models import RawEvent,Instrument
with Session.begin() as s:
    raw=s.scalar(select(RawEvent).where(RawEvent.source=='aster',RawEvent.endpoint.like('%exchangeInfo%')).order_by(RawEvent.received_at.desc()).limit(1))
    excluded=[]
    for row in (raw.payload if raw else {}).get('symbols',[]):
        eligible=row.get('symbolType',0)==0 and not ({'STOCK','COMMODITY','FOREX','INDEX'}&set(row.get('underlyingSubType',[])))
        instrument=s.get(Instrument,'aster:perp:'+row['symbol'])
        if instrument:
            instrument.spec={**instrument.spec,'crypto_eligible':eligible,'underlying_subtype':row.get('underlyingSubType',[]),'symbol_type':row.get('symbolType')}
            if not eligible:excluded.append(instrument.id)
    print({'excluded_from_crypto_pool':len(excluded),'history_preserved':True})
