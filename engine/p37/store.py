from engine.p39.timing import QUOTE_MS,BOOK_MS,REFERENCE_MS,REVIEW_MS,WAIT_REVIEW_MS
"""Short observation transactions. Source receipt and visibility are independent."""
import time
from uuid import uuid4
from sqlalchemy import select,func,cast,BigInteger,case
from engine.db import Session
from engine.models import Observation,Instrument,MarketState,UserPreference,CurrentSignal,Watchlist,Plan,Episode,Campaign,StreamEvent
from engine.repository import emit
from engine.db_retry import retry_transaction
from engine.p37.detector import DEFAULTS,change

VERSION='p37-v1'
def catalog():
    with Session() as s:
        config=s.get(UserPreference,'p37_config')
        from engine.repository import runtime_config
        at=int(time.time()*1000);max_age=REFERENCE_MS
        rows=list(s.execute(select(Instrument,MarketState).join(MarketState).where(Instrument.venue.in_(['binance','binance_spot']),Instrument.quote_asset=='USDT')))
        items={}
        for i,m in rows:
            from engine.pd2.universe_policy import primary_eligible
            if not primary_eligible(i):continue
            if i.native_symbol.endswith(('UPUSDT','DOWNUSDT','BULLUSDT','BEARUSDT')) or i.native_symbol in ('USDCUSDT','FDUSDUSDT','TUSDUSDT','USDPUSDT','DAIUSDT'):continue
            v=next((m.payload[k] for k in ('quote_volume_24h','volume_24h_quote','quote_volume') if m.payload.get(k) is not None),None)
            source=m.payload.get('volume_24h_source_at');received=m.payload.get('volume_24h_received_at')
            status='missing' if v is None else 'source_time_unknown' if not source or not received else 'available' if 0<=at-source<=max_age and 0<=at-received<=max_age else 'stale'
            items[i.id]=dict(asset_id=i.asset_id,symbol=i.native_symbol,venue=i.venue,liquidity=float(v) if v is not None and status=='available' else None,
                liquidity_last_value=float(v) if v is not None else None,
                liquidity_source_at=m.payload.get('volume_24h_source_at'),liquidity_received_at=m.payload.get('volume_24h_received_at'),
                liquidity_status=status,
                quote_asset=i.quote_asset,oi_unit=i.spec.get('base_asset') or i.native_symbol.removesuffix(i.quote_asset),onboard_time=i.spec.get('onboard_time'),state=dict(m.payload))
        return items,{**DEFAULTS,**(config.payload if config else {})}

@retry_transaction
def persist(events,meta,now,config,delivery=None):
    from collections import Counter
    from engine.p40.diagnostics import event_id
    counts=Counter();receipts=[];seen=set()
    if not events:
        if delivery is not None:delivery.update(unique_events=0,qualified_events=0,scope='this committed detection batch')
        return 0
    with Session.begin() as s:
        latest_ids=select(Observation.id).where(Observation.instrument_id.in_([e[0] for e in events])).distinct(Observation.instrument_id,Observation.direction).order_by(Observation.instrument_id,Observation.direction,Observation.last_at.desc(),Observation.id)
        rows=list(s.scalars(select(Observation).where(Observation.id.in_(latest_ids)).order_by(Observation.id).with_for_update()))
        latest={}
        for r in sorted(rows,key=lambda r:r.last_at):latest[(r.instrument_id,r.direction)]=r
        updates=[];demands=[]
        for iid,f,classification in events:
            for direction in sorted({x['direction'] for x in f['reasons']}):
                reasons=[x for x in f['reasons'] if x['direction']==direction];key=(iid,direction);row=latest.get(key)
                ident=event_id(iid,direction,reasons)
                if ident in seen:continue
                seen.add(ident);counts['unique_events']+=1
                suppressed=classification=='market_comovement'
                counts['suppressed' if suppressed else 'qualified_events']+=1
                price=f.get('basis_price');source=max(x['source_at'] for x in reasons)
                basis={'price':price,'at':f.get('basis_at'),'source':f.get('basis_source'),'price_kind':f.get('basis_price_kind')}
                if row and source<=row.payload.get('source_at',0):
                    if not suppressed:counts['already_covered']+=1
                    receipts.append({'event_id':ident,'instrument_id':iid,'observation_id':row.id,'outcome':'newer_endpoint_already_stored'});continue
                if row and row.status!='archived' and source-row.payload.get('source_at',0)<10000 and classification==row.payload.get('classification') and {(x['kind'],x['window']) for x in reasons}=={(x['kind'],x['window']) for x in row.payload.get('reasons',[])}:
                    if not suppressed:counts['coalesced']+=1
                    receipts.append({'event_id':ident,'instrument_id':iid,'observation_id':row.id,'outcome':'coalesced_same_condition'});continue
                created=row is None or now-row.last_at>config['merge_ms']
                if row is None or now-row.last_at>config['merge_ms']:
                    row=Observation(id=uuid4().hex,instrument_id=iid,asset_id=meta[iid]['asset_id'],direction=direction,first_at=source,last_at=source,visible_at=None,status='suppressed',version=now,payload=dict(first_price=str(price) if price is not None else None,first_basis=basis,symbol=meta[iid]['symbol'],rule_version=VERSION,first_rule_config=dict(config),first_reasons=reasons,previous_event_id=row.id if row else None,timeline=[],trigger_count=0))
                    s.add(row);latest[key]=row
                p=dict(row.payload);visible=classification!='market_comovement' or row.visible_at is not None
                if visible and row.visible_at is None:
                    row.visible_at=now;p.update(visible_price=str(price) if price is not None else None,visible_basis=basis,high=str(price) if price is not None else None,low=str(price) if price is not None else None,analysis_status='queued')
                timeline=list(p.get('timeline',[]))
                # Keep transitions, not every tick; repeated conditions have one episode count per minute.
                if not timeline or source-timeline[-1]['at']>=60000:
                    timeline.append(dict(at=source,price=str(price) if price is not None else None,reasons=reasons));p['trigger_count']=p.get('trigger_count',0)+1
                p.update(reasons=list({(r['kind'],r['window']):r for r in p.get('reasons',[])+reasons}.values()),source_at=source,rvol=f['rvol'],oi_change=f['oi_change'],changes=f['changes'],classification=classification,classification_context=f.get('classification_context'),timeline=timeline[-200:])
                row.payload=p;row.last_at=source;row.status='observing' if visible else 'suppressed';row.version=max(now,row.version+1);updates.append(row.id)
                row.payload={**row.payload,'last_rule_config':dict(config),'last_basis':basis,'persisted_at':now,
                    'trigger_event_id':ident,'trigger_evaluated_at':f.get('evaluated_at',now)}
                if not suppressed:counts['created' if created else 'updated']+=1
                receipts.append({'event_id':ident,'instrument_id':iid,'observation_id':row.id,'source_at':source,
                    'persisted_at':now,'outcome':'suppressed' if suppressed else 'created' if created else 'updated'})
                from engine.p39.pipeline import enabled, upsert, observation_source
                if enabled():
                    if visible:
                        s.flush()
                        payload=observation_source(source,row.payload['reasons'],VERSION,config,basis,direction,row.asset_id,now,ident,f.get('evaluated_at',now))
                        demands.append((iid,row.id,source+config['cool_ms'],payload))
        if demands:
            from engine.p39.pipeline import lock_demands,demand_id
            lock_demands(s,[demand_id(iid,'observation',ident) for iid,ident,_,_ in demands])
            for iid,ident,expires,payload in sorted(demands,key=lambda d:demand_id(d[0],'observation',d[1])):
                upsert(s,iid,'observation',ident,now,expires,payload)
        if updates:emit(s,'observation_update',{'ids':updates,'version':now,'trigger_events':receipts[:100]})
    if delivery is not None:
        delivered=sum(counts[k] for k in ('created','updated','already_covered','coalesced'))
        delivery.update(**dict(counts),accounted_qualified=delivered,
            unaccounted=max(0,counts['qualified_events']-delivered),
            accounted_rate=delivered/counts['qualified_events'] if counts['qualified_events'] else None,
            committed_at=int(time.time()*1000),events=receipts[:100],events_truncated=len(receipts)>100,
            scope='this committed detection batch; coalesced events are not new independent markets')
    return len(updates)

def refresh_tracking(now,config):
    from engine.p37.detector import detector
    from types import SimpleNamespace
    with detector.lock:
        prices={iid:q[-1] for iid,q in detector.prices.items() if q}
        oi_windows={iid:detector.delta(q,now,900000,60000,REFERENCE_MS) for iid,q in detector.oi.items()}
    with Session.begin() as s:
        active=s.get(UserPreference,'p37_active')
        active_ids=active.payload.get('instrument_ids',[]) if active else []
        interval=case((Observation.instrument_id.in_(active_ids),60000),else_=300000)
        tracked=func.coalesce(cast(Observation.payload['tracked_at'].as_string(),BigInteger),0)
        # Keep each writer transaction bounded; skip rows currently owned by
        # analysis instead of retaining hundreds of row locks while waiting.
        rows=list(s.scalars(select(Observation).where(Observation.status!='archived',tracked+interval<=now)
            .order_by(tracked,Observation.id).limit(64).with_for_update(skip_locked=True)))
        if not rows:return
        ids={r.instrument_id for r in rows};states={m.instrument_id:m for m in s.scalars(select(MarketState).where(MarketState.instrument_id.in_(ids)))}
        from engine.p38.plan_queries import live_plans
        # Old snapshots can end a plan before its Episode phase catches up.
        # Protection tracking and admission to fresh entry analysis are distinct.
        plans={p.execution_instrument_id for p in s.execute(live_plans(now))}
        entries={p.execution_instrument_id for p in s.execute(live_plans(now,entry=True))}
        watched=set(s.scalars(select(Watchlist.asset_id)))
        signals=list(s.scalars(select(CurrentSignal).where(CurrentSignal.instrument_id.in_(ids))))
        buy=set()
        from decimal import Decimal as D
        for signal in signals:
            state=states.get(signal.instrument_id);zone=signal.payload.get('entry_zone') or [];stop=signal.payload.get('protection')
            if signal.action in ('participate','trial') and signal.valid_until>now and state and 0<=now-state.as_of<=QUOTE_MS and state.payload.get('price') and len(zone)==2 and all(x is not None for x in zone) and stop is not None:
                value=D(str(state.payload['price']))
                if D(str(zone[0]))<=value<=D(str(zone[1])) and value>D(str(stop)):buy.add(signal.instrument_id)
        links={}
        for r in signals:links.setdefault(r.instrument_id,[]).append({'id':r.id,'action':r.action,'valid_until':r.valid_until})
        changed=[]
        for row in rows:
            p=dict(row.payload);previous_status=row.status;m=states.get(row.instrument_id)
            sample=prices.get(row.instrument_id)
            if sample:m=SimpleNamespace(as_of=sample[0],payload={'price':sample[1]})
            age=now-row.last_at;protected=p.get('pinned') or row.asset_id in watched or row.instrument_id in plans
            if not protected and age>=config['archive_ms']:row.status='archived'
            elif row.visible_at and not protected and age>=config['cool_ms']:row.status='cooling'
            elif row.visible_at and protected:row.status='focused'
            elif row.visible_at:row.status='observing'
            if m and 0<=now-m.as_of<=QUOTE_MS and m.payload.get('price'):
                value=str(m.payload['price']);p['price']=value;p['price_at']=m.as_of;p['data_status']='fresh'
                if row.visible_at:
                    from decimal import Decimal as D
                    p['high']=str(max(D(p.get('high') or value),D(value)));p['low']=str(min(D(p.get('low') or value),D(value)))
                    p['since_first_pct']=change(value,p.get('visible_price'))
                    p['max_up_pct']=change(p['high'],p.get('visible_price'));p['max_down_pct']=change(p['low'],p.get('visible_price'))
                tier=60000 if row.instrument_id in active_ids else 300000
                p['tracking_gap']=bool(p.get('last_sample_at') and m.as_of-p['last_sample_at']>tier*2)
                p['last_sample_at']=m.as_of
            else:
                p['data_status']='delayed';p['tracking_gap']=not m or now-m.as_of>max(30000,p.get('tracking_interval_ms',30000)*2)
            p['tracked_at']=now
            p['tracking_interval_ms']=60000 if row.instrument_id in active_ids else 300000
            p['tracking_tier']='active' if row.instrument_id in active_ids else row.status
            p['price_source']='discovery_stream' if sample else 'market_state'
            p['trade_status']='active' if row.instrument_id in entries else 'no_entry'
            p['plan_tracking_status']='entry_open' if row.instrument_id in entries else 'protection_only' if row.instrument_id in plans else 'none'
            p['plans']=links.get(row.instrument_id,[])
            p['buy_eligible']=row.instrument_id in buy and p.get('data_status')=='fresh'
            oi=oi_windows.get(row.instrument_id)
            p.update(oi_change=oi[0] if oi else None,oi_change_start_at=oi[1] if oi else None,
                oi_change_end_at=oi[2] if oi else None,
                oi_change_status='available' if oi else 'not_applicable' if row.instrument_id.startswith(('binance_spot:','binance_alpha:')) else 'insufficient_history')
            if p!=row.payload or row.status!=previous_status:
                row.payload=p;row.version=max(now,row.version+1);changed.append(row.id)
        if changed:emit(s,'observation_update',{'ids':changed,'version':now})

def read_observations(archived=False,pinned=False,since=None,offset=0,limit=200,requested_assets=None):
    from engine.p38.observation_query import read_observations as query
    return query(archived,pinned,since,offset,limit,requested_assets)

def pin(ident,value):
    with Session.begin() as s:
        row=s.get(Observation,ident,with_for_update=True)
        if not row:return False
        row.payload={**row.payload,'pinned':value};row.version=int(time.time()*1000)
        from engine.p39.pipeline import enabled, upsert
        if enabled():upsert(s,row.instrument_id,'manual',row.id,row.version,payload={'observation_id':row.id},state='active' if value else 'ended')
        emit(s,'observation_update',{'ids':[ident],'version':row.version})
    return True


def manual_watch(symbol):
    now=int(time.time()*1000)
    with Session.begin() as s:
        inst=s.scalar(select(Instrument).where(Instrument.venue.in_(['binance','binance_spot']),Instrument.quote_asset=='USDT',Instrument.native_symbol==symbol.upper()).order_by(Instrument.venue).limit(1))
        if not inst:return None
        row=s.scalar(select(Observation).where(Observation.instrument_id==inst.id,Observation.visible_at.is_not(None),Observation.status!='archived').order_by(Observation.last_at.desc()).with_for_update())
        if row:
            row.payload={**row.payload,'pinned':True};row.status='focused';row.version=max(now,row.version+1)
        else:
            market=s.get(MarketState,inst.id)
            price=str(market.payload['price']) if market and 0<=now-market.as_of<=QUOTE_MS and market.payload.get('price') else None
            row=Observation(id=uuid4().hex,instrument_id=inst.id,asset_id=inst.asset_id,direction='manual',first_at=now,last_at=now,visible_at=now,status='focused',version=now,payload={'symbol':inst.native_symbol,'pinned':True,'first_price':price,'visible_price':price,'first_reasons':[],'reasons':[],'timeline':[],'analysis_status':'queued','rule_version':VERSION,'manual':True})
            s.add(row)
        from engine.p39.pipeline import enabled, upsert
        if enabled():
            s.flush();upsert(s,inst.id,'manual',row.id,now,payload={'observation_id':row.id})
        emit(s,'observation_update',{'ids':[row.id],'version':row.version})
        return row.id
