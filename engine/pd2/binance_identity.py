"""Native Binance asset identity; no symbol stripping or vendor aliases."""
from hashlib import sha256
from sqlalchemy import select
from engine.models import Instrument,IdentityLink
from engine.adapters.base import now_ms

def seed(session,instrument_ids=None):
    query=select(Instrument).where(Instrument.venue.in_(['binance','binance_spot','binance_alpha']))
    if instrument_ids is not None:query=query.where(Instrument.id.in_(instrument_ids))
    instruments=list(session.scalars(query))
    existing=set(session.scalars(select(IdentityLink.asset_id).where(IdentityLink.provider=='binance',IdentityLink.asset_id.in_([i.asset_id for i in instruments]))))
    additions=[]
    for inst in instruments:
        spec=inst.spec or {}
        if inst.venue=='binance_alpha':
            if not spec.get('chain_id') or not spec.get('contract_address'):continue
            external='contract:'+sha256(inst.asset_id.encode()).hexdigest()
            method='Binance Alpha exact chain and contract'
        else:
            if spec.get('base_asset')!=inst.asset_id:continue
            external='native:'+inst.asset_id;method='Binance native exchangeInfo baseAsset'
        if inst.asset_id in existing:continue
        row=dict(asset_id=inst.asset_id,provider='binance',external_id=external,status='verified',
            evidence={'source':method,'instrument_id':inst.id,'unit_policy':'native_exchange_unit','multiplier':'1'},known_at=now_ms())
        additions.append(row);existing.add(inst.asset_id)
    if additions:
        from sqlalchemy.dialects.postgresql import insert
        session.execute(insert(IdentityLink).values(additions).on_conflict_do_nothing(index_elements=['asset_id','provider']))
