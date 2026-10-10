"""Compare actual two-sided native execution cost at the same USD amount."""
from decimal import Decimal as D
from sqlalchemy import select,func,text
from engine.db import Session
from engine.models import Instrument,DataCapability,UserPreference,Candle
from engine.pd2.decision import walk_book,sell_quantity
from engine.pd2.providers import quote_usd_rate
from engine.adapters.base import now_ms

def compare_books(candidates,notional_usd,now,max_age=10000):
    accepted=[];excluded=[]
    for candidate in candidates:
        book=candidate.get('book') or {};fx=candidate.get('quote_usd_rate');fee=candidate.get('fee_rate')
        reason=None
        if not fx or D(str(fx))<=0:reason='fx_missing'
        elif fee is None:reason='fee_unknown'
        elif book.get('scope')!='execution_market':reason='not_native_execution_book'
        elif not __import__('engine.pd2.book_clock',fromlist=['fresh']).fresh(book,now,max_age):reason='depth_stale'
        elif not book.get('asks') or not book.get('bids'):reason='missing_depth'
        if reason:excluded.append({'instrument_id':candidate['instrument_id'],'reason':reason});continue
        buy=walk_book(book['asks'],D(str(notional_usd))/D(str(fx)))
        sell=sell_quantity(book['bids'],buy['quantity'])
        if not buy['complete'] or not sell['complete']:
            excluded.append({'instrument_id':candidate['instrument_id'],'reason':'insufficient_depth'});continue
        a,b=D(buy['average']),D(sell['average'])
        if b>a or b<=0:
            excluded.append({'instrument_id':candidate['instrument_id'],'reason':'invalid_book'});continue
        cost=(a-b)/a+2*D(str(fee))
        accepted.append({'instrument_id':candidate['instrument_id'],'roundtrip_cost_bps':str(cost*10000),'quantity':buy['quantity'],'buy_average':str(a),'sell_average':str(b),'quote_usd_rate':str(fx),'observed_at':book.get('observed_at'),'snapshot_ref':book.get('source_raw_ref') or str((book.get('received_at'),book.get('observed_at'))),'source_time_kind':book.get('source_time_kind','exchange')})
    return {'ranked':sorted(accepted,key=lambda r:(D(r['roundtrip_cost_bps']),r['instrument_id'])),'excluded':excluded,'notional_usd':str(notional_usd),'scope':'available_verified_same_asset_markets'}

def select_main(asset,config,now=None):
    now=now or now_ms();candidates=[]
    with Session.begin() as s:
        s.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':'main_market:'+asset})
        instruments=list(s.scalars(select(Instrument).where(Instrument.asset_id==asset)))
        for i in instruments:
            from engine.pd2.universe_policy import primary_eligible
            if not primary_eligible(i):continue
            history=dict(s.execute(select(Candle.resolution,func.count()).where(Candle.instrument_id==i.id,Candle.is_closed==True,Candle.resolution.in_(["60","240"])).group_by(Candle.resolution)).all())
            if any(history.get(r,0)<60 for r in ("60","240")):continue
            cached=s.get(DataCapability,f'{i.venue}:execution_book:{i.id}')
            candidates.append({'instrument_id':i.id,'book':cached.payload if cached and cached.status=='fresh' else None,'quote_usd_rate':quote_usd_rate(i.quote_asset,s,now),'fee_rate':config.execution.taker_fee_rates.get(i.venue)})
        comparison=compare_books(candidates,config.execution.probe_notional_usd,now,config.execution.depth_max_age_seconds*1000)
        key='main_market:'+asset;state=s.get(UserPreference,key);old=state.payload if state else {}
        ranked=comparison['ranked'];best=ranked[0] if ranked else None;current=next((r for r in ranked if r['instrument_id']==old.get('selected')),None)
        proposed=best['instrument_id'] if best else None
        snapshot=[[r['instrument_id'],r['snapshot_ref']] for r in ranked]
        streak=old.get('streak',0) if snapshot==old.get('comparison_snapshot') else (old.get('streak',0)+1) if proposed and old.get('proposed')==proposed else 1
        selected=old.get('selected') if current else proposed
        if best and current and proposed!=selected:
            improvement=D(current['roundtrip_cost_bps'])-D(best['roundtrip_cost_bps'])
            if improvement>=max(D(2),D(current['roundtrip_cost_bps'])*D('.2')) and streak>=3:selected=proposed
        perp_ids={i.id for i in instruments if i.market_type in ('linear_perpetual','perpetual')}
        best_perp=next((r['instrument_id'] for r in ranked if r['instrument_id'] in perp_ids),None)
        payload={**comparison,'comparison_snapshot':snapshot,'selected_perpetual':best_perp,'selected':selected,'proposed':proposed,'streak':streak,'observed_at':now,'switch_rule':'three distinct snapshots; >=20% and >=2bps improvement','existing_plan_policy':'never_rebind'}
        s.merge(UserPreference(key=key,payload=payload,updated_at=now));return payload
