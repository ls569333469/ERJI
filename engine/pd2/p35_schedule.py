"""Layered discovery and current-event dependencies, without extra upstream budget."""
from sqlalchemy import select,cast,BigInteger
from decimal import Decimal as D
from engine.adapters.base import now_ms
from engine.models import UserPreference,CurrentSignal,Campaign,Episode
from engine.pd2.analysis import technical


def background(session,instrument,bars,now,config,prepared=None):
    key='p35_background:'+instrument.id;old=session.get(UserPreference,key)
    signature=[[tf, now//(604800000 if tf=='1W' else 14400000 if tf=='240' else 86400000), [[b['open_time'],b.get('revision'),b.get('raw_ref')] for b in bars.get(tf,[])]] for tf in ('1W','1D','240')]
    if old and old.payload.get('signature')==signature:return old.payload
    states={}
    for tf in ('1W','1D'):
        a=prepared[tf] if prepared and tf in prepared else technical(bars.get(tf,[]),tf,now,instrument.spec.get('tick_size') or '1',config)
        states[tf]={k:a.get(k) for k in ('status','direction','momentum')}
    daily=states['1D'];weekly=states['1W']
    kind='data_pending' if daily.get('status')!='fresh' else 'trend_up' if daily.get('direction')=='up' and weekly.get('direction')=='up' else 'countertrend' if daily.get('direction')=='up' and weekly.get('direction')=='down' else 'bottom_repair' if daily.get('momentum')=='strengthening' else 'mixed_or_down'
    from engine.strategies.structures import volume_multiple
    reasons=[]
    for tf in ('1D','240'):
        rows=bars.get(tf,[])
        if len(rows)>=21 and 0<=now-rows[-1]['close_time']<=(14400000 if tf=='240' else 86400000)+180000:
            multiple=volume_multiple(rows[-1],rows[:-1])
            if multiple is not None and D(str(multiple))>=D('1.5') and D(rows[-1]['close'])>D(rows[-1]['open']):
                reasons.append({'timeframe':tf,'kind':'closed_price_volume_anomaly','relative_volume':str(multiple),'close_time':rows[-1]['close_time'],'reason':'已收盘上涨伴随成交额至少1.5倍，进入小周期检查，不代表买入'})
    payload={'trigger_inspection_reasons':reasons,'needs_trigger_data':bool(reasons),'priority_until':now+14400000 if reasons else now,'signature':signature,'as_of':now,'timeframes':states,'background':kind,'veto':False}
    session.merge(UserPreference(key=key,updated_at=now,payload=payload));return payload


def priority_instruments(session):
    ids=set(session.scalars(select(Campaign.reference_instrument_id).join(Episode).where(Episode.phase.notin_(('ended','invalidated')))))
    ids.update(session.scalars(select(CurrentSignal.instrument_id).where(CurrentSignal.action.in_(('participate','trial','waiting','no_chase')),CurrentSignal.payload['opportunity_eligible'].as_boolean().is_(True))))
    ids.update(session.scalars(select(UserPreference.key).where(UserPreference.key.startswith('p35_background:',autoescape=True),UserPreference.payload['needs_trigger_data'].as_boolean().is_(True),cast(UserPreference.payload['priority_until'].as_string(),BigInteger)>now_ms())))
    ids={iid.removeprefix('p35_background:') for iid in ids}
    return ids


def target_priority(target,active):
    # Tracked events keep their small-period dependencies; discovery starts with background.
    rank={'60':0,'240':1,'1D':2,'1W':3} if target[0] in active else {'1W':0,'1D':1,'240':2,'60':3}
    return (0 if target[0] in active else 1,rank.get(target[2],4),target[1])


def windows(config):
    from engine.strategies.p33_lifecycle import DEPENDENCIES
    required={'1W':60,'1D':60,'240':60}
    for rule,deps in DEPENDENCIES.items():
        if rule in config.disabled_strategies:continue
        for tf,count in deps.items():required[tf]=max(required.get(tf,0),count+2,60)
    # Stable EMA/MACD seed and target pivots; not lifetime history.
    return {tf:max(count,300) for tf,count in required.items()}


def execution_assets(session,now,asset_ids=None):
    """Keep tracking all structures, but fetch books only for a usable nearby entry."""
    from engine.models import MarketState
    ids=set()
    query=select(Campaign.asset_id,Episode.rule_version,MarketState.as_of,MarketState.payload['price'],
        *(Episode.evidence[k] for k in ('price_pattern','trigger','expires_at','entry_zone','threshold','atr'))).select_from(Episode).join(Campaign,Episode.campaign_id==Campaign.id).join(MarketState,MarketState.instrument_id==Campaign.reference_instrument_id).where(Episode.phase.notin_(('ended','invalidated')))
    if asset_ids is not None:query=query.where(Campaign.asset_id.in_(asset_ids))
    for asset,rule,stamp,price,pattern,trigger,expires,zone,boundary,atr in session.execute(query):
        if not pattern and not rule.startswith('p33-'):
            ids.add(asset);continue
        if price is None or not 0<=now-stamp<=180000:continue
        if trigger:
            if (expires or 0)>now and zone and D(zone[0])<=D(price)<=D(zone[1]):ids.add(asset)
        elif boundary and atr and abs(D(price)-D(boundary))<=D(atr):ids.add(asset)
    return ids


def hourly_dependencies(session,config):
    from engine.strategies.p33_lifecycle import DEPENDENCIES
    ids=set()
    for rule,strategy,iid in session.execute(select(Episode.rule_version,Episode.strategy_id,Campaign.reference_instrument_id).join(Campaign).where(Episode.phase.notin_(('ended','invalidated')))):
        if not rule.startswith('p33-') or ('60' in DEPENDENCIES.get(strategy,{}) and strategy not in config.disabled_strategies):ids.add(iid)
    ids.update(k.removeprefix('p35_background:') for k in session.scalars(select(UserPreference.key).where(UserPreference.key.startswith('p35_background:',autoescape=True),UserPreference.payload['needs_trigger_data'].as_boolean().is_(True),cast(UserPreference.payload['priority_until'].as_string(),BigInteger)>now_ms())))
    return ids
