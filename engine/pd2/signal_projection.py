from engine.repository import write_heartbeat
from engine.p39.timing import QUOTE_MS,BOOK_MS,REFERENCE_MS,REVIEW_MS,WAIT_REVIEW_MS
"""Small persisted signal rows. GET never runs collectors or technical analysis."""
from decimal import Decimal as D
from sqlalchemy import select, case, func, cast, Numeric, or_, and_, delete, text
from hashlib import sha256
from uuid import uuid4
from collections import OrderedDict
from threading import Lock
from engine.db import Session
from engine.models import CurrentSignal, MarketState, WorkerHeartbeat,Instrument,Candle,SourceHealth
from engine.adapters.base import now_ms
from engine.pd2.universe_policy import membership
from engine.p40.diagnostics import scan_view

LABELS = {'buy': '建议买入', 'waiting': '等待买入', 'risk': '风险提醒', 'history': '历史信号'}
_list_snapshots=OrderedDict();_snapshot_lock=Lock();_SNAPSHOT_LIMIT=512


def compact(row):
    market, evidence = row['market'], row['evidence']
    opportunity = row.get('opportunity_plan') or {}
    derivatives=row.get('derivatives') or {}
    oi = derivatives.get('cross_market_oi') or {}
    funding=derivatives.get('funding_reference') or {}
    derivative_market=derivatives.get('derivatives_reference_market') or {}
    native=derivatives.get('native_reference') or {}
    oi_times=[m['observed_at'] for m in oi.get('markets',[]) if isinstance(m.get('observed_at'),int)]
    # A multi-market total carries its oldest contributing sample, not the
    # independently selected funding contract's (possibly stale) timestamp.
    oi_as_of=min(oi_times) if oi.get('total_oi_usd') is not None and oi_times else market.get('oi_as_of')
    timeframes=[]
    for assessment in row.get('timeframe_assessments') or evidence.get('timeframe_assessments', []):
        if assessment.get('timeframe') not in ('60','240','1D','1W'):
            continue
        timeframes.append({k: assessment.get(k) for k in (
            'timeframe','status','direction','momentum','macd_cross_confirmed','roc_5_pct','volume_multiple')})
    zone=evidence.get('entry_zone')
    if zone and len(zone)>=2 and row.get("phase")!="structure_pending":
        target_basis=(D(str(zone[0]))+D(str(zone[1])))/2
        target_basis_kind='entry_zone_midpoint'
    elif evidence.get('threshold'):
        target_basis=D(str(evidence['threshold']));target_basis_kind='pattern_trigger'
    elif (evidence.get('box') or {}).get('trigger_price'):
        target_basis=D(str(evidence['box']['trigger_price']))
        target_basis_kind='breakout_trigger'
    else:
        target_basis=D(str(market['price'])) if market.get('price') is not None else None
        target_basis_kind='current_price_fallback'
    box=evidence.get('box') or {}
    protection_reference=evidence.get('protection_reference')
    if not protection_reference and box.get('upper') is not None and box.get('atr') is not None:
        protection_reference={
            'price':str(D(str(box['upper']))-D(str(box['atr']))*D('0.25')),
            'status':'estimated',
            'method':'box_upper_minus_0.25_atr',
            'timeframe':'4H',
        }
    invalidation=evidence.get('invalidation')
    if not invalidation and box.get('lower') is not None:
        invalidation={'price':str(box['lower']),'timeframe':'4H','condition':'close_below','method':'frozen_box_lower'}

    selected_t1=None
    if row.get('phase')=='structure_pending' and target_basis is not None:
        candidates=[]
        for key,default_tf in (('targets_t1','4H'),('targets_4h','4H'),('targets_1d','1D'),('targets_1w','1W')):
            value=evidence.get(key)
            if not value or value.get('low') is None:
                continue
            low=D(str(value['low']))
            if low>target_basis:
                candidates.append((low,default_tf,value))
        if candidates:
            _,default_tf,value=min(candidates,key=lambda item:item[0])
            selected_t1={**value,'timeframe':value.get('timeframe') or default_tf,
                         'selection_method':'nearest_valid_resistance_above_plan_basis'}

    def target(name):
        evidence_key={'t1':'targets_4h','daily':'targets_1d','weekly':'targets_1w'}[name]
        default_tf={'t1':'4H','daily':'1D','weekly':'1W'}[name]
        t = selected_t1 if name=='t1' and row.get('phase')=='structure_pending' else opportunity.get(name) or (evidence.get('targets_t1') if name=='t1' else None) or evidence.get(evidence_key)
        if not t:return None
        result={k:t.get(k) for k in ('low','high','method','timeframe','known_at','kind','selection_method','id','anchor','structural_version')}
        result['method']=result.get('method') or result.get('kind')
        result['timeframe']=result.get('timeframe') or default_tf
        result['basis_price']=str(target_basis) if target_basis is not None else None
        result['basis_kind']=target_basis_kind
        result['remaining_upside_pct']=(str((D(str(t['low']))/target_basis-1)*100)
            if target_basis is not None and target_basis>0 and t.get('low') is not None else None)
        return result
    projected_targets={k:target(k) for k in ('t1','daily','weekly')}
    threshold=D(str(row.get('candidate_min_net_r','1.8')))
    preliminary_net_r=None
    opportunity_eligible=False
    eligibility_reason='not_waiting_candidate'
    if row.get('phase')=='structure_pending':
        t1=projected_targets['t1']
        if not row.get('fresh',True):
            eligibility_reason='market_data_stale'
        elif target_basis is None:
            eligibility_reason='plan_basis_missing'
        elif not protection_reference or protection_reference.get('price') is None:
            eligibility_reason='protection_reference_missing'
        else:
            risk=target_basis-D(str(protection_reference['price']))
            if risk<=0:
                eligibility_reason='protection_reference_not_below_plan_basis'
            else:
                if t1 and t1.get('low') is not None:
                    preliminary_net_r=(D(str(t1['low']))-target_basis)/risk
                # Waiting candidates remain waiting; target/R estimates do not
                # hide them, and cannot promote them to a buy action.
                opportunity_eligible=True
                eligibility_reason='eligible'
    reason=(row['action'].get('reason') or '')[:240]
    distance=None
    trigger=(evidence.get('box') or {}).get('trigger_price')
    if row.get('phase')=='structure_pending' and market.get('price') is not None and trigger:
        price=D(str(market['price']));trigger_value=D(str(trigger))
        distance=str((trigger_value/price-1)*100) if price>0 else None
        volume=evidence.get('pending_volume_multiple');required=(evidence.get('trigger_requirements') or {}).get('relative_volume_min','1.5')
        volume_text=(f'当前量能 {D(str(volume)):.2f} 倍，规则要求至少 {D(str(required)):.2f} 倍。' if volume is not None
                     else '最近 4H 量能基线不足，暂不能确认。')
        pending_state=evidence.get('pending_state')
        state_text=("曾有 4H 收盘越过触发价，但当时成交量未达确认标准，因此没有形成有效突破。" if pending_state=='close_above_volume_unconfirmed' else
                    "曾在 4H K 线内越过触发价，但收盘未站稳，因此没有形成有效突破。" if pending_state=='intrabar_above_close_rejected' else
                    "最近 4H 收盘已进入箱体上沿附近，但尚未越过完整触发缓冲。" if pending_state=='near_trigger' else
                    "尚未出现符合规则的 4H 收盘突破。")
        position_text=(f'现价低于触发价 {abs(D(distance)):.2f}%。' if D(distance)>=0 else f'现价高于触发价 {abs(D(distance)):.2f}%，仍需等待 4H 收盘和量能同时确认。')
        plan_text=(f'最近有效阻力为 {projected_targets["t1"]["low"]}（{projected_targets["t1"]["timeframe"]}），'
                   f'初步盈亏比 {preliminary_net_r:.2f}。' if projected_targets['t1'] and preliminary_net_r is not None else '')
        reason=(f'等待买入。{state_text}{position_text}需要 4H 收盘高于 {trigger}。'
                f'{volume_text}4H 收盘跌破 {(invalidation or {}).get("price",box.get("lower"))} 后取消候选。{plan_text}')
    if evidence.get('price_pattern') and row.get('phase')=='structure_pending':
        reason=evidence.get('reason',reason)
        if market.get('price') and evidence.get('threshold'):
            distance=str((D(evidence['threshold'])/D(str(market['price']))-1)*100)
    if evidence.get('strategy_id') and row.get('phase')!='structure_pending':
        opportunity_eligible=row['action']['code'] in ('waiting','no_chase') and row.get('fresh',False)
        eligibility_reason='active_pattern_plan' if opportunity_eligible else eligibility_reason
    return {
        'id': row['id'], 'asset_id': row['asset_id'], 'instrument_id': row['instrument_id'],
        'snapshot_id': row.get('snapshot_id'), 'event_id': row['id'],
        'plan_id':row.get('plan_id'),'pipeline_tier':'T2' if row.get('phase')=='structure_pending' else 'T3',
        'origin':row.get('origin') or evidence.get('origin') or {'origin_type':'legacy_unlinked'},
        'strategy_id': evidence.get('template') or row.get('strategy_id'),
        'strategy_version': evidence.get('strategy_version') or (evidence.get('box') or {}).get('rule_version') or row.get('strategy_version'),
        'symbol': market.get('symbol', row['asset_id']),
        'venue': market['venue'], 'quote_asset': market['quote_asset'], 'price': market.get('price'),
        'action_code': row['action']['code'], 'reason': reason,
        'entry_type': '小仓买入' if row['action']['code'] == 'trial' else '买入条件已确认' if row['action']['code'] == 'participate' else None,
        'phase': row['phase'], 'phase_label': row['phase_label'],
        'timeframes': timeframes,
        'entry_zone': evidence.get('entry_zone'), 'protection': evidence.get('protection'),
        'protection_reference': protection_reference, 'invalidation': invalidation,
        'trigger_requirements': evidence.get('trigger_requirements'), 'distance_to_trigger_pct': distance,
        'pending_state':evidence.get('pending_state'),
        'pending_volume_multiple':evidence.get('pending_volume_multiple'),
        'box': {k: box.get(k) for k in ('upper', 'lower', 'trigger_price', 'atr', 'rule_version')},
        'first_seen_at': row['first_seen_at'], 'first_seen_price': row['first_seen_price'],
        'since_first_pct': row.get('since_first_pct'), 'as_of': market.get('as_of'),
        'targets': projected_targets,
        'preliminary_net_r':str(preliminary_net_r) if preliminary_net_r is not None else None,
        'candidate_min_net_r':str(threshold),
        'opportunity_eligible':opportunity_eligible,
        'opportunity_eligibility_reason':eligibility_reason,
        'oi_usd': oi.get('total_oi_usd') if 'total_oi_usd' in oi else market.get('oi_notional_usd'),
        'oi_notional_quote': market.get('oi_notional_quote'), 'oi_quote_asset':market.get('oi_quote_asset'),
        'oi_cap_ratio': oi.get('oi_cap_ratio') if 'oi_cap_ratio' in oi else market.get('oi_cap_ratio'),
        'oi_scope': oi.get('scope') or ('已覆盖市场合计' if oi.get('total_oi_usd') is not None else '当前市场'),
        'native_oi':native,
        'circulating_cap':market.get('circulating_cap'),
        'contract_status':derivative_market.get('contract_status','unmapped'),
        'oi_sources':sorted({m.get('venue') or m.get('source') for m in oi.get('markets',[]) if m.get('venue') or m.get('source')}),
        'oi_status':oi.get('status') or market.get('oi_quality',{}).get('status'),
        'oi_quantity_status':oi.get('quantity_status'),
        'oi_coverage_hash':oi.get('coverage_hash'),
        'oi_market_count':len(oi.get('markets') or []),
        'oi_changes': [{'period':v.get('period'),'display_label':v.get('period'),
                        'value_pct':v.get('quantity_change_pct'),'notional_change_pct':v.get('notional_change_pct'),
                        'price_change_pct':v.get('price_change_pct'),'status':v.get('quantity_status',v.get('status')),
                        'notional_status':v.get('status'),'start_at':v.get('start_at'),'end_at':v.get('end_at'),
                        'quantity_endpoints':v.get('quantity_endpoints',[])}
                       for v in oi.get('changes', [])] if oi.get('changes') is not None else
                      [{k:v.get(k) for k in ('period','display_label','value_pct','price_change_pct','status')}
                       for v in market.get('oi_changes', [])],
        'oi_as_of': oi_as_of, 'valuation_status': market.get('valuation_status'),
        'funding_rate': funding.get('rate',market.get('funding_rate')), 'funding_interval_seconds': funding.get('interval_seconds',market.get('funding_interval_seconds')),
        'funding_reference_instrument':funding.get('instrument_id',row['instrument_id']),
        'derivatives_reference_market':derivative_market,
        'funding_settled_at':funding.get('settled_at',market.get('funding_settled_at')),
        'funding_status':funding.get('status'),
        'expires_at': evidence.get('expires_at'), 'validation_status': 'unvalidated', 'probability': None,
        'decision_as_of': row.get('decision_as_of', evidence.get('evaluated_at')),
        'structure_as_of': evidence.get('structure_as_of',evidence.get('evaluated_at')),
        'structure_valid_until': evidence.get('structure_valid_until'),
        'data_support':{
            'price':{'kind':'observed','instrument_id':row['instrument_id'],'source':market.get('venue'),'as_of':market.get('as_of')},
            'structure':{'kind':'derived','method':evidence.get('strategy_id') or 'closed_4h_frozen_box','rule_version':evidence.get('strategy_version') or (evidence.get('box') or {}).get('rule_version'),'as_of':evidence.get('known_at',evidence.get('structure_as_of',evidence.get('evaluated_at')))},
            'targets':{'kind':'derived','basis_kind':target_basis_kind,'basis_price':str(target_basis) if target_basis is not None else None},
            'derivatives':{'kind':'observed_and_derived','instrument_id':derivative_market.get('instrument_id'),'as_of':derivative_market.get('observed_at'),'status':oi.get('status')}
        }
    }


def publish(rows,scope=None,built_at=None,replace_scope=True):
    now = built_at if built_at is not None else now_ms()
    with Session.begin() as session:
        if scope is None:session.execute(text('SELECT pg_advisory_xact_lock(1877225)'))
        else:
            session.execute(text('SELECT pg_advisory_xact_lock_shared(1877225)'))
            for iid in sorted(scope):session.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'projection:'+iid})
        aliases, admitted = membership(session)
        keep = [];changed=False
        previous={r.id:r for r in session.scalars(select(CurrentSignal).where(CurrentSignal.instrument_id.in_(scope)) if scope is not None else select(CurrentSignal))}
        specs={i.id:i.spec for i in session.scalars(select(Instrument).where(
            Instrument.id.in_([row['instrument_id'] for row in rows])))} if rows else {}
        for row in rows:
            canonical = aliases.get(row['asset_id'], row['asset_id'])
            # Auxiliary-only catalog entries cannot publish independent opportunities.
            if row['market'].get('venue') not in ('binance','binance_spot','binance_alpha','wallet_rising'):
                continue
            if canonical not in admitted and row['action']['code']!='ended':
                # A delisting or quality downgrade stops new entries, not plan tracking.
                row={**row,'action':{'code':'paused','reason':'主池资格已变化，暂停新买入；原信号及保护计划继续保留，等待市场状态核实。'}}
            payload = compact(row); payload['asset_id'] = canonical
            payload['tick_size'] = (specs.get(row['instrument_id']) or {}).get('tick_size')
            until = row.get('valid_until') or (row.get('evidence',{}).get('evaluated_at') or now)+360000
            if row['action']['code'] in ('trial', 'participate'):
                until = min(until, (row.get('execution') or {}).get('received_at', 0) + BOOK_MS,
                            row.get('evidence', {}).get('expires_at') or 0)
            payload['valid_until'] = until
            key = row.get('plan_id') or row['id']; keep.append(key)
            old=previous.get(key)
            if old and old.computed_at>now:continue
            if old and old.payload==payload and old.action==row['action']['code'] and old.valid_until==until:continue
            changed=True
            session.merge(CurrentSignal(id=key, asset_id=canonical, instrument_id=row['instrument_id'],
                action=row['action']['code'], first_seen_at=row['first_seen_at'], computed_at=now,
                valid_until=until, payload=payload))
        # Remove obsolete projections only. Plans, snapshots and first reports remain immutable.
        removal=delete(CurrentSignal).where(CurrentSignal.id.notin_(keep),CurrentSignal.computed_at<=now)
        if scope is not None:removal=removal.where(CurrentSignal.instrument_id.in_(scope))
        if replace_scope:session.execute(removal)
        changed=changed or (replace_scope and bool(set(previous)-set(keep)))
        write_heartbeat(session,'signal_projection',now,'ok',{'rows':len(keep)})
        from engine.repository import emit
        if changed:emit(session, 'signal_update', {'version': now, 'rows': len(keep)})
    return len(keep)

def refresh_coverage():
    """Low-priority coverage accounting, deliberately outside projection writes."""
    now=now_ms()
    from engine.pd2.universe_policy import primary_eligible,PRIMARY
    with Session.begin() as session:
        aliases,admitted=membership(session)
        catalog=list(session.scalars(select(Instrument)))
        eligible=[i for i in catalog if primary_eligible(i)]
        ids=[i.id for i in eligible]
        counts={(i,r):n for i,r,n in session.execute(select(Candle.instrument_id,Candle.resolution,func.count()).where(
            Candle.is_closed==True,Candle.instrument_id.in_(ids)).group_by(Candle.instrument_id,Candle.resolution))} if ids else {}
        ready=sum(all(counts.get((i.id,r),0)>=50 for r in ('60','240','1D','1W')) for i in eligible)
        from engine.pd2.source_policy import REALTIME
        health=list(session.scalars(select(SourceHealth).where(SourceHealth.source.in_(REALTIME))))
        from engine.models import DataCapability
        from engine.pd2.source_health import supplement_health
        supplemental={}
        successes={h.source:h.last_success_at for h in health if h.source not in supplemental}
        successes.update({name:record['last_success_at'] for name,record in supplemental.items()})
        coverage={'computed_at':now,'unique_assets':len(admitted),'eligible_markets':len(eligible),
            'history_ready_markets':ready,'history_pending_or_short':len(eligible)-ready,
            'auxiliary_unmapped':sum(i.venue not in PRIMARY and aliases.get(i.asset_id,i.asset_id) not in admitted for i in catalog),
            'source_failures':[h.source for h in health if h.source not in supplemental and h.state in ('error','partial')]+[name for name,r in supplemental.items() if r['state'] in ('error','partial')],
            'source_last_success':successes,
            'history_policy':'50 closed bars per native timeframe is minimum assessment, not full strategy validation'}
        write_heartbeat(session,'signal_coverage',now,'ok',coverage)
    return coverage

def _snapshot(signature,ids,now,snapshot_id=None):
    with _snapshot_lock:
        expired=[key for key,value in _list_snapshots.items() if value['expires_at']<=now]
        for key in expired:_list_snapshots.pop(key,None)
        saved=_list_snapshots.get(snapshot_id) if snapshot_id else None
        if saved and saved['signature']==signature:return snapshot_id,saved['ids']
        token=uuid4().hex
        _list_snapshots[token]={'signature':signature,'ids':ids,'expires_at':now+3600000}
        while len(_list_snapshots)>_SNAPSHOT_LIMIT:_list_snapshots.popitem(last=False)
        return token,ids


def read(view='stages', query='', sort='first', page=1, size=25, snapshot_id=None):
    now = now_ms(); c = CurrentSignal; m = MarketState
    price = cast(m.payload['price'].as_string(), Numeric)
    stop = cast(c.payload['protection'].as_string(), Numeric)
    buying = c.action.in_(['participate', 'trial'])
    decision_as_of=cast(c.payload['decision_as_of'].as_string(),Numeric)
    structure_until=func.coalesce(cast(c.payload['structure_valid_until'].as_string(),Numeric),decision_as_of+4_200_000)
    unsafe = or_(c.computed_at > now, m.as_of.is_(None), price.is_(None),
                 and_(buying, or_(c.valid_until <= now, m.as_of < now-QUOTE_MS, m.as_of > now,
                     price <= stop, stop.is_(None),
                     cast(c.payload['entry_zone'][0].as_string(), Numeric).is_(None),
                     price < cast(c.payload['entry_zone'][0].as_string(), Numeric),
                     price > cast(c.payload['entry_zone'][1].as_string(), Numeric))),
                 and_(~buying, m.as_of < now-360000),
                 and_(c.action.in_(['waiting','no_chase']),structure_until<now))
    category = case((c.action == 'ended', 'history'),
                    (c.action.in_(['paused', 'expired']), 'risk'),
                    (unsafe, 'risk'),
                    (c.action.in_(['participate', 'trial']), 'buy'), else_='waiting')
    opportunity_level = case(
        (and_(category == 'buy', c.action == 'participate'), 'l1'),
        (and_(category == 'waiting', c.action.in_(['waiting', 'no_chase']),
              c.payload['opportunity_eligible'].as_boolean().is_(True)), 'l2'),
        (and_(category == 'buy', c.action == 'trial'), 'l3'),
        else_=None)
    # A newer unqualified structure must not hide a valid candidate for the asset.
    rank = case((category == 'buy', 0), (opportunity_level == 'l2', 1),
                (category == 'waiting', 2), (category == 'risk', 3), else_=4)
    base = select(c.id, c.asset_id, c.first_seen_at, c.payload, c.valid_until, c.computed_at,
                  price.label('current_price'), m.as_of.label('quote_as_of'),
                  category.label('category'), opportunity_level.label('opportunity_level'),
                  func.row_number().over(partition_by=c.asset_id, order_by=(rank, c.computed_at.desc(), c.id)).label('rn'))
    base=base.outerjoin(m,m.instrument_id==c.instrument_id)
    if view!='history':
        # Old projections are preserved as evidence, never a realtime fallback.
        base=base.join(Instrument,Instrument.id==c.instrument_id).where(
            Instrument.venue.in_(('binance','binance_spot','binance_alpha')))
    base=base.subquery()
    # History contains separate episodes; current views have one primary plan per asset.
    conditions = [base.c.rn == 1] if view != 'history' else []
    wanted = {'recommended':'buy', 'trial':'buy', 'buy':'buy', 'waiting':'waiting', 'risk':'risk', 'history':'history'}.get(view)
    if view == 'opportunities':
        conditions.append(base.c.opportunity_level.is_not(None))
    else:
        conditions.append(base.c.category == wanted if wanted else base.c.category != 'history')
    if query.strip():
        pattern = '%' + query.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'
        conditions.append(or_(base.c.asset_id.ilike(pattern, escape='\\'), base.c.payload['symbol'].as_string().ilike(pattern, escape='\\')))
    order = ((case((base.c.opportunity_level == 'l1', 0), (base.c.opportunity_level == 'l2', 1),
                   (base.c.opportunity_level == 'l3', 2), else_=3),
              base.c.first_seen_at.desc(), base.c.asset_id, base.c.id)
             if view == 'opportunities' else (base.c.first_seen_at.desc(), base.c.asset_id, base.c.id))
    if sort == 'oi':
        order = (cast(base.c.payload['oi_usd'].as_string(), Numeric).desc().nullslast(), *order)
    if sort == 'space':
        order = (cast(base.c.payload['targets']['t1']['remaining_upside_pct'].as_string(), Numeric).desc().nullslast(), *order)
    with Session() as session:
        signature = sha256((view+'|'+query.strip().lower()+'|'+sort).encode()).hexdigest()
        fresh_records=list(session.execute(select(base).where(*conditions).order_by(*order)).mappings()) if snapshot_id is None and view=='opportunities' and page==1 else None
        current_ids=[r['id'] for r in fresh_records] if fresh_records is not None else list(session.scalars(select(base.c.id).where(*conditions).order_by(*order)))
        all_total=len(current_ids)
        snapshot_id,ids=_snapshot(signature,current_ids,now,snapshot_id)
        current_set=set(current_ids)
        surviving=[key for key in ids if key in current_set]
        total=len(surviving)
        pages = max(1, (total+size-1)//size); page = min(page, pages)
        page_ids=surviving[(page-1)*size:page*size]
        fixed_order = case({key:i for i,key in enumerate(page_ids)}, value=base.c.id, else_=len(page_ids)) if page_ids else base.c.id
        # Recheck current safety in SQL. Cohort ordering is fixed, never its price or eligibility.
        records = fresh_records[(page-1)*size:page*size] if fresh_records is not None else (session.execute(select(base).where(*conditions,base.c.id.in_(page_ids)).order_by(fixed_order)).mappings() if page_ids else [])
        rows = []
        for i, r in enumerate(records):
            value = dict(r['payload']); value.update(category=r['category'], action_label=LABELS[r['category']],
                opportunity_level=r['opportunity_level'], valid_until=r['valid_until'], row_number=(page-1)*size+i+1)
            if r['current_price'] is not None:
                value.update(price=str(r['current_price']), as_of=r['quote_as_of'])
                first = D(str(value.get('first_seen_price') or 0))
                value['since_first_pct'] = str((r['current_price']/first-1)*100) if first>0 else None
            if r['category'] == 'risk' and value['action_code'] in ('participate','trial','waiting','no_chase'):
                value.update(action_code='paused', entry_type=None, reason='行情、计划有效期或保护条件已变化，暂停新买入；原计划保护继续')
            rows.append(value)
        # Enrich current display, including older projections, without rewriting
        # immutable snapshots or changing admission/decision predicates.
        from engine.asset_classification import classification
        instruments={inst.id:inst for inst in session.scalars(select(Instrument).where(
            Instrument.id.in_({row['instrument_id'] for row in rows})))} if rows else {}
        for value in rows:
            inst=instruments.get(value['instrument_id'])
            value.update(classification(value['instrument_id'],value['asset_id'],inst.spec if inst else None))
        if view!='history':
            from engine.pd2.current_market_fields import enrich
            enrich(session,rows,now)
        beat = session.get(WorkerHeartbeat, 'signals')
        projection = session.get(WorkerHeartbeat, 'signal_projection')
        coverage = session.get(WorkerHeartbeat, 'signal_coverage')
        # One aggregate scan replaces three repeated JSON/rank evaluations.
        counts={};level_counts={};history_count=0
        primary=base.c.rn==1
        for category_value,level,is_primary,count in session.execute(select(base.c.category,base.c.opportunity_level,primary,func.count()).group_by(base.c.category,base.c.opportunity_level,primary)):
            if category_value=='history':history_count+=count
            if is_primary:
                counts[category_value]=counts.get(category_value,0)+count
                if level:level_counts[level]=level_counts.get(level,0)+count
        counts['history']=history_count
        return {'as_of':now,'rows':rows,'view':view,'groups':[{'id':k,'label':v,'count':counts.get(k,0)} for k,v in LABELS.items()],
            'opportunity_counts':{k:level_counts.get(k,0) for k in ('l1','l2','l3')},
            'scan':{**scan_view(beat.details if beat else {}),'as_of':beat.updated_at if beat else None},
            'projection':{'as_of':projection.updated_at if projection else None,'status':'available' if projection else 'initializing',
                'coverage':coverage.details if coverage else {}},
            'pagination':{'page':page,'page_size':size,'pages':pages,'total':total,'list_snapshot':snapshot_id,
                'new_assets_waiting':max(0,all_total-total),'ordering':'fixed_order_latest_safety'}}
