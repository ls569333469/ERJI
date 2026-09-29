"""Admission and verified aliases. An auxiliary listing is never pool membership."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from sqlalchemy import select
from engine.models import Instrument, IdentityLink
from engine.universe import STABLE_BASES

PRIMARY = {'binance', 'binance_spot', 'binance_alpha', 'wallet_rising'}


def primary_eligible(inst):
    spec = inst.spec or {}
    if inst.venue not in PRIMARY or spec.get('crypto_eligible') is False:
        return False
    if spec.get('status', 'TRADING') not in ('TRADING', 'active'):
        return False
    if spec.get('base_asset', inst.asset_id) in STABLE_BASES:
        return False
    if inst.venue in ('binance_alpha', 'wallet_rising'):
        try:
            return (Decimal(str(spec.get('circulating_cap_usd'))) >= 1000000
                    and Decimal(str(spec.get('liquidity_usd'))) >= 100000)
        except (InvalidOperation, TypeError):
            return False
    return inst.quote_asset in ('USDT', 'USDC') and inst.market_type in ('spot', 'linear_perpetual')


def resolve(instruments, links):
    """Retain native IDs/history; collapse only verified external identity groups."""
    instruments = list(instruments)
    primary = {i.asset_id for i in instruments if primary_eligible(i)}
    groups = defaultdict(set)
    for link in links:
        if link.provider == 'cmc' and link.status == 'verified':
            groups[link.external_id].add(link.asset_id)
    aliases = {i.asset_id: i.asset_id for i in instruments}
    for assets in groups.values():
        roots = sorted(assets & primary, key=lambda a: (':' in a, a))
        if roots:
            for asset in assets:
                aliases[asset] = roots[0]
    admitted = {aliases[a] for a in primary}
    return aliases, admitted


def membership(session):
    return resolve(session.scalars(select(Instrument)), session.scalars(select(IdentityLink)))


def allowed_native_assets(session):
    aliases, admitted = membership(session)
    return {asset for asset, canonical in aliases.items() if canonical in admitted}


def auxiliary_symbols(venue):
    from engine.db import Session
    with Session() as session:
        aliases, admitted = membership(session)
        return {i.native_symbol for i in session.scalars(select(Instrument).where(Instrument.venue == venue))
                if aliases.get(i.asset_id, i.asset_id) in admitted}
