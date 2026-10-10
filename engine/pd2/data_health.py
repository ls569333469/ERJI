"""Background-only field coverage audit; HTTP reads a compact persisted summary."""
from collections import Counter
from decimal import Decimal,InvalidOperation
from hashlib import sha256
from sqlalchemy import select,func
from engine.db import Session
from engine.models import Instrument,MarketState,Candle,IdentityLink,DataCapability,CurrentSignal,UserPreference,RawEvent
from engine.adapters.base import now_ms
from engine.pd2.universe_policy import membership,primary_eligible
from engine.p39.timing import QUOTE_MS,BOOK_MS

PERIODS={'60':3600000,'240':14400000,'1D':86400000,'1W':604800000}
def expected_close(now,tf):
    step=PERIODS[tf];offset=345600000 if tf=='1W' else 0
    return (now-offset)//step*step+offset

def refresh():
    now=now_ms()
    with Session.begin() as s:
        aliases,admitted=membership(s)
        inst=[i for i in s.scalars(select(Instrument)) if i.venue in
              ('binance','binance_spot','binance_alpha','wallet_rising') and primary_eligible(i)
              and aliases.get(i.asset_id,i.asset_id) in admitted]
        ids=[i.id for i in inst]
        states={r.instrument_id:r.payload for r in s.scalars(select(MarketState).where(MarketState.instrument_id.in_(ids)))}
        closes={(iid,tf):at for iid,tf,at in s.execute(select(Candle.instrument_id,Candle.resolution,func.max(Candle.close_time))
            .where(Candle.instrument_id.in_(ids),Candle.is_closed==True,Candle.close_time<=now)
            .group_by(Candle.instrument_id,Candle.resolution))}
        books={r.subject:r for r in s.scalars(select(DataCapability).where(DataCapability.capability=='execution_book',DataCapability.subject.in_(ids)))}
        projected=dict(s.execute(select(CurrentSignal.instrument_id,func.max(CurrentSignal.computed_at)).group_by(CurrentSignal.instrument_id)).all())
        counts={};assets={};rows=[];reasons=Counter()
        for i in inst:
            p=states.get(i.id,{})
            c=counts.setdefault(i.venue,Counter());c['markets']+=1;assets.setdefault(i.venue,set()).add(aliases.get(i.asset_id,i.asset_id))
            def recent(at,age):return isinstance(at,int) and 0<=now-at<=age
            row={'instrument_id':i.id,'asset_id':i.asset_id,'venue':i.venue,'reasons':[]}
            book=books.get(i.id)
            from engine.pd2.book_clock import effective_time,fresh
            book_time=effective_time(book.payload) if book else None
            book_quote=bool(book and book.status=='fresh' and fresh(book.payload,now,BOOK_MS) and book.payload.get('asks'))
            quote=book_quote or recent(p.get('as_of'),QUOTE_MS) and p.get('price_kind') in ('ask','last')
            c['execution_quote_90s']+=int(quote);row['quote_status']='fresh' if quote else 'stale_or_nonexecution'
            c['depth_90s']+=int(bool(book_quote and book.payload.get('bids')))
            row['execution_book']={'status':book.status if book else 'not_collected','observed_at':book.observed_at if book else None,
                'effective_at':book_time,'source_time_kind':book.payload.get('source_time_kind','exchange') if book else None,'raw_ref':book.raw_ref if book else None}
            if not quote:row['reasons'].append('quote_stale_or_nonexecution')
            if i.market_type in ('linear_perpetual','perpetual'):
                oi=recent(p.get('oi_as_of'),360000) and p.get('oi_base') is not None
                c['oi_applicable']+=1;c['oi_6m']+=int(oi);row['oi_status']='fresh' if oi else 'missing_or_stale'
                if not oi:row['reasons'].append('oi_missing_or_stale')
            else:row['oi_status']='linked_contract_checked_in_decision'
            identity=i.venue in ('binance','binance_spot','binance_alpha') or i.spec.get('identity_source')=='Binance Wallet exact chain and contract'
            c['identity_verified']+=int(identity)
            try:positive_supply=Decimal(str(i.spec.get('circulating_supply') or 0))>0
            except (InvalidOperation,ValueError):positive_supply=False
            valuation=bool(positive_supply and recent(i.spec.get('quality_received_at'),86400000))
            c['circulating_supply_current']+=int(valuation)
            row['identity']={'status':'verified' if identity else 'unmapped','provider':'binance',
                'external_id':i.spec.get('base_asset',i.asset_id),'evidence':i.spec.get('identity_source') or 'Binance native exchangeInfo'}
            row['valuation']={'status':'available' if valuation else 'binance_supply_not_provided' if not positive_supply else 'stale',
                'observed_at':i.spec.get('quality_received_at')}
            row['oi']={'quantity':p.get('oi_base'),'notional_quote':p.get('oi_notional_quote'),
                'quote_asset':p.get('oi_quote_asset'),'observed_at':p.get('oi_as_of'),'raw_ref':p.get('oi_raw_ref'),
                'status':p.get('oi_status'),'notional_status':p.get('oi_notional_status')}
            row['funding']={'rate':p.get('funding_rate'),'kind':p.get('funding_kind'),
                'settled_at':p.get('funding_settled_at'),'interval_seconds':p.get('funding_interval_seconds')}
            if not valuation:row['reasons'].append('binance_supply_not_provided' if not positive_supply else 'circulating_supply_stale')
            row['latest_closed']={}
            for tf in PERIODS:
                at=closes.get((i.id,tf));expected=expected_close(now,tf)
                current=at is not None and at>=expected
                c['latest_'+tf]+=int(current)
                row['latest_closed'][tf]={'observed':at,'expected':expected,'current':current}
                if not current:row['reasons'].append('latest_closed_missing:'+tf)
            row['projection_at']=projected.get(i.id)
            c['projection_6m']+=int(recent(row['projection_at'],360000))
            reasons.update(row['reasons']);rows.append(row)
        venues=[{'venue':v,**dict(c),'assets':len(assets[v])} for v,c in sorted(counts.items())]
        summary={'as_of':now,'version':'p40-data-health-v1','venues':venues,'reason_counts':dict(reasons),
                 'ttl_ms':{'execution_quote':QUOTE_MS,'execution_book':BOOK_MS,'t1_price_continuity':15000},
                 'markets':len(rows),'unique_assets':len(set().union(*assets.values())) if assets else 0,
                 'scope':'eligible_instruments_not_unique_tokens','coverage_kind':'latest_closed_not_full_continuity',
                 'examples':[r for r in rows if r['reasons']][:12]}
        s.merge(UserPreference(key='p34_data_health',updated_at=now,payload={'summary':summary,'rows':rows}))
        key=sha256(('p34_data_health:'+str(now//300000)).encode()).hexdigest()
        if not s.get(RawEvent,key):s.add(RawEvent(id=key,source='erji',endpoint='p34_data_health',received_at=now,payload=summary))
    return summary
