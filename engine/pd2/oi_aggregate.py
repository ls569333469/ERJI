"""Native-first OI on verified asset identities. Coverage is part of every value."""
from decimal import Decimal as D
from hashlib import sha256
import json
ALIASES={'binance':'binance','aster':'aster','aster-dex':'aster','hyperliquid':'hyperliquid','okx':'okx'}
def aggregate_oi(native,vendor,now,max_age=180000):
    result=[];excluded=list(vendor.get('excluded',[]));native_keys=set();native_venues=set()
    for r in native:
        if r.get('notional_usd') is None or not 0<=now-r.get('observed_at',0)<=max_age:continue
        if D(r['notional_usd'])<0:continue
        key=(r['venue'],r['native_pair']);native_keys.add(key);native_venues.add(r['venue'])
        result.append({**r,'market_key':r['instrument_id'],'source':'native'})
    for r in vendor.get('markets',[]):
        venue=ALIASES.get(r['venue'],r['venue']);key=(venue,r.get('native_pair'))
        if key in native_keys:excluded.append({'market_key':r['market_key'],'reason':'replaced_by_native'});continue
        if venue in native_venues:
            # Unresolved same-venue contract identity is never added speculatively.
            excluded.append({'market_key':r['market_key'],'reason':'possible_native_overlap_unresolved'});continue
        result.append(r)
    keys=sorted(r['market_key'] for r in result);total=sum(D(r['notional_usd']) for r in result)
    for r in result:r['share_pct']=str(D(r['notional_usd'])/total*100) if total else None
    return {'status':'fresh' if result else 'unavailable','total_oi_usd':str(total) if result else None,'markets':result,'excluded':excluded,'coverage_hash':sha256(json.dumps(keys).encode()).hexdigest()[:16],'scope':'verified_covered_markets_not_global','native_priority':True,'truncated':vendor.get('truncated',False),'quantity_total':str(sum(D(str(r['quantity'])) for r in result)) if result and all(r.get('quantity') is not None for r in result) else None,'quantity_status':'native_base_quantity' if result and all(r.get('quantity') is not None for r in result) else 'vendor_quantities_unavailable'}

def compare_snapshots(old,new):
    if not old:return {'status':'insufficient_history','notional_change_pct':None,'quantity_change_pct':None}
    if old.get('coverage_hash')!=new.get('coverage_hash'):return {'status':'coverage_changed','notional_change_pct':None,'quantity_change_pct':None}
    a,b=old.get('total_oi_usd'),new.get('total_oi_usd')
    qa,qb=old.get('quantity_total'),new.get('quantity_total')
    result={'status':'available' if a is not None and D(a)>0 and b is not None else 'insufficient_history','notional_change_pct':str((D(b)/D(a)-1)*100) if a is not None and D(a)>0 and b is not None else None,'quantity_change_pct':str((D(qb)/D(qa)-1)*100) if qa is not None and qb is not None and D(qa)>0 else None,'meaning':'同覆盖集合；美元名义变化不是资金净流入'}
    before={r['market_key']:r for r in old.get('markets',[])};parts=[]
    for row in new.get('markets',[]):
        prior=before.get(row['market_key']);q0=prior.get('quantity') if prior else None;q1=row.get('quantity')
        if q0 is None or q1 is None or D(str(q0))<=0 or D(str(q1))<=0:continue
        q0,q1=D(str(q0)),D(str(q1));p0=D(prior['notional_usd'])/q0;p1=D(row['notional_usd'])/q1
        parts.append({'market_key':row['market_key'],'quantity_contribution_usd':str((q1-q0)*p0),'price_fx_contribution_usd':str(q0*(p1-p0)),'interaction_usd':str((q1-q0)*(p1-p0)),'formula':'dN = dQ*P0 + Q0*dP + dQ*dP; P is USD value per base unit'})
    result['decomposition']=parts;result['decomposition_status']='complete' if parts and len(parts)==len(new.get('markets',[])) else 'partial_or_quantity_unavailable'
    return result

def history_changes(asset,current,now):
    from engine.db import Session
    from engine.models import RawEvent
    from sqlalchemy import select
    result=[]
    with Session() as s:
        for label,duration in [('1H',3600000),('4H',14400000),('1D',86400000)]:
            row=s.scalar(select(RawEvent).where(RawEvent.source=='erji',RawEvent.endpoint=='cross_oi:'+asset,RawEvent.received_at<=now-duration,RawEvent.received_at>=now-duration-180000).order_by(RawEvent.received_at.desc()).limit(1))
            result.append({'period':label,'start_at':row.received_at if row else None,'end_at':now,**compare_snapshots(row.payload if row else None,current)})
    return result

def record_snapshot(session,asset,current,now):
    from engine.models import RawEvent
    from sqlalchemy.dialects.postgresql import insert
    key=sha256(('cross_oi:'+asset+':'+str(now//60000)).encode()).hexdigest()
    session.execute(insert(RawEvent).values(id=key,source='erji',endpoint='cross_oi:'+asset,received_at=now,payload=current).on_conflict_do_nothing(index_elements=['id']))
