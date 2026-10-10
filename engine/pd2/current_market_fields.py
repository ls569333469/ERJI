"""Batch local facts for current lists, independent of frozen decision evidence.

No HTTP, technical analysis, admission changes or snapshot writes on this path.
"""
from decimal import Decimal as D
from sqlalchemy import select, values, column, String, BigInteger, true
from engine.models import Instrument, MarketState, IdentityLink, OiPoint, DataCapability
from engine.pd2.binance_facts import positive, quote_usd_rate, SUPPLY_MAX_AGE
from engine.pd2.universe_policy import resolve, primary_eligible

from engine.p39.timing import REFERENCE_MS,QUOTE_MS
OI_MAX_AGE=REFERENCE_MS
CHANGE_MAX_AGE=REFERENCE_MS
PERIODS={'15M':900000,'1H':3600000,'4H':14400000,'1D':86400000}


def quantity(value):
    try:
        number=D(str(value))
        return number if number.is_finite() and number>=0 else None
    except (ArithmeticError,ValueError,TypeError):return None


def oi_clock(payload,now):
    at=payload.get('oi_as_of');value=quantity(payload.get('oi_base'))
    if not isinstance(at,int):return 'not_collected'
    if at>now:return 'clock_invalid'
    if now-at>OI_MAX_AGE:return 'stale'
    return 'fresh' if value is not None else 'not_returned'


def funding_clock(payload,now):
    at=payload.get('funding_settled_at');interval=payload.get('funding_interval_seconds')
    if not isinstance(at,int):return 'not_collected'
    if at>now:return 'clock_invalid'
    rate=payload.get('funding_rate')
    try:valid=rate is not None and D(str(rate)).is_finite()
    except ArithmeticError:valid=False
    if not valid:return 'not_returned'
    if not isinstance(interval,(int,float)) or interval<=0:return 'interval_unknown'
    return 'fresh' if now-at<=interval*1000+180000 else 'stale'


def quantity_changes(session,references,now,periods=None,max_age=OI_MAX_AGE):
    """One indexed lateral probe per endpoint, batched into one SQL statement."""
    periods=periods or PERIODS
    targets=[]
    for iid,p in references.items():
        end=p.get('oi_as_of')
        if oi_clock(p,now)!='fresh' or not isinstance(end,int) or not 0<=now-end<=max_age:continue
        for label,duration in periods.items():targets.append((iid,label,end-duration,60000 if label=='15M' else 300000))
    endpoints={}
    if targets:
        demand=values(column('iid',String),column('period',String),column('target',BigInteger),column('tolerance',BigInteger),name='oi_endpoints').data(targets)
        point=select(OiPoint.event_time,OiPoint.received_at,OiPoint.base_quantity,OiPoint.raw_ref).where(
            OiPoint.instrument_id==demand.c.iid,OiPoint.event_time<=demand.c.target,
            OiPoint.event_time>=demand.c.target-demand.c.tolerance,OiPoint.received_at<=now,
            OiPoint.base_quantity>0).order_by(OiPoint.event_time.desc()).limit(1).lateral()
        for row in session.execute(select(demand.c.iid,demand.c.period,point).select_from(demand.outerjoin(point,true()))).mappings():
            endpoints[(row['iid'],row['period'])]=row
    result={}
    for iid,p in references.items():
        end=p.get('oi_as_of');q=quantity(p.get('oi_base'));clock=oi_clock(p,now)
        changes=[]
        for label in periods:
            before=endpoints.get((iid,label));start=before['event_time'] if before else None
            valid=clock=='fresh' and isinstance(end,int) and 0<=now-end<=max_age and q is not None and start is not None
            status='available' if valid else clock if clock!='fresh' else 'stale' if isinstance(end,int) and now-end>max_age else 'insufficient_history'
            changes.append({'period':label,'display_label':label,'value_pct':str((q/D(before['base_quantity'])-1)*100) if valid else None,
                'status':status,'start_at':start,'end_at':end,'instrument_id':iid,
                'duration_ms':end-start if valid else None,'endpoint_tolerance_ms':60000 if label=='15M' else 300000,
                'method':'same_native_contract_quantity_endpoints','start_raw_ref':before['raw_ref'] if before else None,
                'source':'Binance','known_at':before['received_at'] if before else None})
        result[iid]=changes
    return result


def enrich(session,rows,now):
    if not rows:return
    ids={r['instrument_id'] for r in rows}
    # One catalog load handles aliases; work/history is per distinct contract,
    # not per repeated observation event. Retired catalog entries cannot be references.
    catalog=list(session.scalars(select(Instrument).where(Instrument.venue.in_(('binance','binance_spot','binance_alpha')))))
    instruments={i.id:i for i in catalog}
    links=list(session.scalars(select(IdentityLink).where(IdentityLink.provider=='binance',IdentityLink.status=='verified')))
    aliases,_=resolve(catalog,links)
    by_asset={}
    for i in catalog:
        if i.venue=='binance' and primary_eligible(i):by_asset.setdefault(aliases.get(i.asset_id,i.asset_id),[]).append(i)
    chosen={}
    for iid in ids:
        inst=instruments.get(iid)
        if not inst:continue
        options=by_asset.get(aliases.get(inst.asset_id,inst.asset_id),[])
        chosen[iid]=next((i for i in options if i.id==iid),None) or min(options,key=lambda i:(i.quote_asset!='USDT',i.id),default=None)
    wanted=ids|{i.id for i in chosen.values() if i}
    states={s.instrument_id:s.payload for s in session.scalars(select(MarketState).where(MarketState.instrument_id.in_(wanted)))}
    records={r.key:r for r in session.scalars(select(DataCapability).where(DataCapability.key.in_(
        ('binance:fx:USDTUSD','binance:fx:USDCUSD','binance:supply:web_products'))))}
    fx={quote:quote_usd_rate(quote,session,now,records) for quote in ('USD','USDT','USDC')}
    references={i.id:states.get(i.id,{}) for i in chosen.values() if i}
    changes=quantity_changes(session,references,now)
    supply_record=records.get('binance:supply:web_products')
    web_supplies=(supply_record.payload.get('assets') or {}) if supply_record and 0<=now-supply_record.received_at<=SUPPLY_MAX_AGE else {}
    for row in rows:
        inst=instruments.get(row['instrument_id']);ref=chosen.get(row['instrument_id']);p=states.get(ref.id,{}) if ref else {}
        status=oi_clock(p,now) if ref else 'unmapped'
        notional=quantity(p.get('oi_notional_quote'))
        native={'instrument_id':ref.id if ref else None,'status':status,'quantity':p.get('oi_base') if ref else None,
            'notional_quote':p.get('oi_notional_quote') if ref else None,'quote_asset':ref.quote_asset if ref else None,
            'observed_at':p.get('oi_as_of'),'source':'Binance','fx_status':'available' if ref and fx.get(ref.quote_asset) else 'missing_or_stale' if ref else None}
        usd=notional*fx[ref.quote_asset] if ref and status=='fresh' and notional is not None and fx.get(ref.quote_asset) else None
        oi_status=status if status!='fresh' else 'fx_missing_or_stale' if ref and not fx.get(ref.quote_asset) else 'notional_unavailable' if notional is None else 'fresh'
        own=states.get(row['instrument_id'],{});price=positive(own.get('price'));at=own.get('as_of')
        spec=inst.spec if inst else {};supply=positive(spec.get('circulating_supply'));supply_at=spec.get('quality_received_at')
        valuation_kind='binance_alpha_circulating_supply' if supply is not None else None;supply_asset=inst.asset_id if inst else row.get('asset_id')
        meta=web_supplies.get(supply_asset,{})
        if supply is None and meta:
            # Website metadata is advisory and has receipt time, not a fabricated
            # exchange observation timestamp. Never use its old ticker price.
            supply=positive(meta.get('supply'));supply_at=supply_record.received_at;valuation_kind='binance_web_reported_supply_reference'
        cap=None;valuation_status='binance_supply_not_provided'
        if supply is not None:
            if not isinstance(supply_at,int) or not 0<=now-supply_at<=SUPPLY_MAX_AGE:valuation_status='supply_stale'
            elif not inst or not fx.get(inst.quote_asset):valuation_status='fx_missing_or_stale'
            elif price is None or not isinstance(at,int) or not 0<=now-at<=QUOTE_MS:valuation_status='price_stale'
            else:cap=supply*price*fx[inst.quote_asset];valuation_status='reference' if valuation_kind.endswith('_reference') else 'fresh'
        row.update(native_oi=native,contract_status='verified' if ref else 'unmapped',
            oi_status=oi_status,oi_as_of=p.get('oi_as_of'),oi_usd=str(usd) if usd is not None else None,
            oi_notional_quote=p.get('oi_notional_quote') if ref else None,oi_quote_asset=ref.quote_asset if ref else None,
            oi_sources=['binance'] if ref else [],oi_scope='binance_reference_contract',oi_market_count=1 if usd is not None else 0,
            oi_changes=[x for x in changes.get(ref.id,[]) if x['period']!='15M'] if ref and status=='fresh' else [],
            circulating_cap=str(cap) if cap is not None else None,oi_cap_ratio=str(usd/cap) if usd is not None and cap else None,
            valuation_status=valuation_status,valuation_kind=valuation_kind,valuation_received_at=supply_at,
            valuation_observed_at=None if valuation_kind and valuation_kind.endswith('_reference') else supply_at,
            valuation_source='Binance web product metadata' if valuation_kind and valuation_kind.endswith('_reference') else 'Binance Alpha' if supply is not None else None,
            funding_rate=p.get('funding_rate') if ref else None,funding_settled_at=p.get('funding_settled_at'),
            funding_interval_seconds=p.get('funding_interval_seconds'),funding_reference_instrument=ref.id if ref else None,
            funding_status=funding_clock(p,now) if ref else 'unmapped',
            current_fields_as_of=now)
        # Explicitly distinguish unmapped native units; no ticker-prefix guessing.
        if not ref:row['contract_missing_reason']='no_verified_binance_contract_identity'
        if 'oi_change' in row or 'visible_at' in row:
            own_perp=inst and inst.venue=='binance' and primary_eligible(inst)
            item=next((c for c in changes.get(ref.id,[]) if c['period']=='15M'),None) if own_perp and ref else None
            row.update(oi_change=float(item['value_pct']) if item and item['value_pct'] is not None and isinstance(p.get('oi_as_of'),int) and 0<=now-p['oi_as_of']<=CHANGE_MAX_AGE else None,
                oi_change_status=item['status'] if item and isinstance(p.get('oi_as_of'),int) and 0<=now-p['oi_as_of']<=CHANGE_MAX_AGE else 'stale' if own_perp and p.get('oi_as_of') else 'not_collected' if own_perp else 'not_applicable',
                oi_change_start_at=item['start_at'] if item else None,oi_change_end_at=item['end_at'] if item else None)
            if inst and inst.market_type=='spot':row['oi_status']='not_applicable'
