"""Small persisted signal rows. GET never runs collectors or technical analysis."""
from decimal import Decimal as D
from sqlalchemy import select, case, func, cast, Numeric, or_, and_, delete, text
from hashlib import sha256
from uuid import uuid4
from engine.db import Session
from engine.models import CurrentSignal, MarketState, WorkerHeartbeat, DataCapability,Instrument,Candle,SourceHealth
from engine.adapters.base import now_ms
from engine.pd2.universe_policy import membership

LABELS = {'buy': '建议买入', 'waiting': '等待买入', 'risk': '风险提醒', 'history': '历史信号'}


def compact(row):
    market, evidence = row['market'], row['evidence']
    opportunity = row.get('opportunity_plan') or {}
    oi = ((row.get('derivatives') or {}).get('cross_market_oi') or {})
    funding=(row.get('derivatives') or {}).get('funding_reference') or {}
    def target(name):
        t = opportunity.get(name)
        return {k: t.get(k) for k in ('low', 'high', 'remaining_upside_pct', 'method', 'timeframe')} if t else None
    return {
        'id': row['id'], 'asset_id': row['asset_id'], 'instrument_id': row['instrument_id'],
        'snapshot_id': row.get('snapshot_id'), 'symbol': market.get('symbol', row['asset_id']),
        'venue': market['venue'], 'quote_asset': market['quote_asset'], 'price': market.get('price'),
        'action_code': row['action']['code'], 'reason': (row['action'].get('reason') or '')[:240],
        'entry_type': '小仓买入' if row['action']['code'] == 'trial' else '买入条件已确认' if row['action']['code'] == 'participate' else None,
        'phase': row['phase'], 'phase_label': row['phase_label'],
        'entry_zone': evidence.get('entry_zone'), 'protection': evidence.get('protection'),
        'box': {k: (evidence.get('box') or {}).get(k) for k in ('upper', 'lower', 'trigger_price')},
        'first_seen_at': row['first_seen_at'], 'first_seen_price': row['first_seen_price'],
        'since_first_pct': row.get('since_first_pct'), 'as_of': market.get('as_of'),
        'targets': {k: target(k) for k in ('t1', 'daily', 'weekly')},
        'oi_usd': oi.get('total_oi_usd') or market.get('oi_notional_usd'),
        'oi_notional_quote': market.get('oi_notional_quote'), 'oi_quote_asset':market.get('oi_quote_asset'),
        'oi_cap_ratio': oi.get('oi_cap_ratio') if oi.get('oi_cap_ratio') is not None else market.get('oi_cap_ratio'),
        'oi_scope': '已覆盖市场合计' if oi.get('total_oi_usd') is not None else '当前市场',
        'oi_changes': [{k: v.get(k) for k in ('period', 'display_label', 'value_pct', 'price_change_pct')}
                       for v in market.get('oi_changes', [])],
        'oi_as_of': market.get('oi_as_of'), 'valuation_status': market.get('valuation_status'),
        'funding_rate': funding.get('rate',market.get('funding_rate')), 'funding_interval_seconds': funding.get('interval_seconds',market.get('funding_interval_seconds')),
        'funding_reference_instrument':funding.get('instrument_id',row['instrument_id']),
        'funding_settled_at':funding.get('settled_at',market.get('funding_settled_at')),
        'expires_at': evidence.get('expires_at'), 'validation_status': 'unvalidated', 'probability': None,
        'decision_as_of': row.get('decision_as_of', evidence.get('evaluated_at')),
    }


def publish(rows):
    now = now_ms()
    with Session.begin() as session:
        session.execute(text('SELECT pg_advisory_xact_lock(1877225)'))
        aliases, admitted = membership(session)
        keep = [];changed=False
        previous={r.id:r for r in session.scalars(select(CurrentSignal))}
        for row in rows:
            canonical = aliases.get(row['asset_id'], row['asset_id'])
            # Auxiliary-only catalog entries cannot publish independent opportunities.
            if row['market'].get('venue') not in ('binance','binance_spot','binance_alpha','wallet_rising'):
                continue
            if canonical not in admitted and row['action']['code']!='ended':
                # A delisting or quality downgrade stops new entries, not plan tracking.
                row={**row,'action':{'code':'paused','reason':'主池资格已变化，暂停新买入；原信号及保护计划继续保留，等待市场状态核实。'}}
            payload = compact(row); payload['asset_id'] = canonical
            until = row.get('valid_until') or (row.get('evidence',{}).get('evaluated_at') or now)+360000
            if row['action']['code'] in ('trial', 'participate'):
                until = min(until, (row.get('execution') or {}).get('received_at', 0) + 10000,
                            row.get('evidence', {}).get('expires_at') or 0)
            payload['valid_until'] = until
            key = row.get('plan_id') or row['id']; keep.append(key)
            old=previous.get(key)
            if old and old.payload==payload and old.action==row['action']['code'] and old.valid_until==until:continue
            changed=True
            session.merge(CurrentSignal(id=key, asset_id=canonical, instrument_id=row['instrument_id'],
                action=row['action']['code'], first_seen_at=row['first_seen_at'], computed_at=now,
                valid_until=until, payload=payload))
        # Remove obsolete projections only. Plans, snapshots and first reports remain immutable.
        session.execute(delete(CurrentSignal).where(CurrentSignal.id.notin_(keep)))
        changed=changed or bool(set(previous)-set(keep))
        existing=session.get(WorkerHeartbeat,'signal_projection')
        coverage=(existing.details if existing else {}).get('coverage',{})
        if coverage.get('computed_at',0)+60000<=now:
            from engine.pd2.universe_policy import primary_eligible,PRIMARY
            catalog=list(session.scalars(select(Instrument)))
            eligible=[i for i in catalog if primary_eligible(i)]
            counts={(i,r):n for i,r,n in session.execute(select(Candle.instrument_id,Candle.resolution,func.count()).where(Candle.is_closed==True,Candle.instrument_id.in_([i.id for i in eligible])).group_by(Candle.instrument_id,Candle.resolution))}
            ready=sum(all(counts.get((i.id,r),0)>=50 for r in ('60','240','1D','1W')) for i in eligible)
            health=list(session.scalars(select(SourceHealth)))
            coverage={'computed_at':now,'unique_assets':len(admitted),'eligible_markets':len(eligible),
                'history_ready_markets':ready,'history_pending_or_short':len(eligible)-ready,
                'auxiliary_unmapped':sum(i.venue not in PRIMARY and aliases.get(i.asset_id,i.asset_id) not in admitted for i in catalog),
                'source_failures':[h.source for h in health if h.state in ('error','partial')],
                'source_last_success':{h.source:h.last_success_at for h in health},
                'history_policy':'50 closed bars per native timeframe is minimum assessment, not full strategy validation'}
        session.merge(WorkerHeartbeat(role='signal_projection', updated_at=now, state='ok', details={'rows': len(keep),'coverage':coverage}))
        from engine.repository import emit
        if changed:emit(session, 'signal_update', {'version': now, 'rows': len(keep)})
    return len(keep)


def read(view='stages', query='', sort='first', page=1, size=25, snapshot_id=None):
    now = now_ms(); c = CurrentSignal; m = MarketState
    price = cast(m.payload['price'].as_string(), Numeric)
    stop = cast(c.payload['protection'].as_string(), Numeric)
    buying = c.action.in_(['participate', 'trial'])
    unsafe = or_(c.computed_at > now, m.as_of.is_(None), price.is_(None),
                 and_(buying, or_(c.valid_until <= now, m.as_of < now-15000, m.as_of > now,
                     price <= stop, stop.is_(None),
                     cast(c.payload['entry_zone'][0].as_string(), Numeric).is_(None),
                     price < cast(c.payload['entry_zone'][0].as_string(), Numeric),
                     price > cast(c.payload['entry_zone'][1].as_string(), Numeric))),
                 and_(~buying, m.as_of < now-360000),
                 cast(c.payload['decision_as_of'].as_string(),Numeric)<now-360000)
    category = case((c.action == 'ended', 'history'),
                    (c.action.in_(['paused', 'expired']), 'risk'),
                    (unsafe, 'risk'),
                    (c.action.in_(['participate', 'trial']), 'buy'), else_='waiting')
    rank = case((category == 'buy', 0), (category == 'waiting', 1), (category == 'risk', 2), else_=3)
    base = select(c.id, c.asset_id, c.first_seen_at, c.payload, c.valid_until, c.computed_at,
                  price.label('current_price'), m.as_of.label('quote_as_of'),
                  category.label('category'),
                  func.row_number().over(partition_by=c.asset_id, order_by=(rank, c.computed_at.desc(), c.id)).label('rn'))
    base = base.outerjoin(m, m.instrument_id == c.instrument_id).subquery()
    # History contains separate episodes; current views have one primary plan per asset.
    conditions = [base.c.rn == 1] if view != 'history' else []
    wanted = {'recommended':'buy', 'trial':'buy', 'buy':'buy', 'waiting':'waiting', 'risk':'risk', 'history':'history'}.get(view)
    conditions.append(base.c.category == wanted if wanted else base.c.category != 'history')
    if query.strip():
        pattern = '%' + query.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'
        conditions.append(or_(base.c.asset_id.ilike(pattern, escape='\\'), base.c.payload['symbol'].as_string().ilike(pattern, escape='\\')))
    order = (base.c.first_seen_at.desc(), base.c.asset_id, base.c.id)
    if sort == 'oi':
        order = (cast(base.c.payload['oi_usd'].as_string(), Numeric).desc().nullslast(), *order)
    if sort == 'space':
        order = (cast(base.c.payload['targets']['t1']['remaining_upside_pct'].as_string(), Numeric).desc().nullslast(), *order)
    with Session.begin() as session:
        signature = sha256((view+'|'+query.strip().lower()+'|'+sort).encode()).hexdigest()
        saved = session.get(DataCapability, 'erji:list:'+str(snapshot_id)) if snapshot_id else None
        if not saved or saved.expires_at <= now or saved.payload.get('signature') != signature:
            # Freeze only IDs/order, never safety or prices. Query payload only for the page below.
            ids = list(session.scalars(select(base.c.id).where(*conditions).order_by(*order)))
            snapshot_id = uuid4().hex
            session.add(DataCapability(key='erji:list:'+snapshot_id, provider='erji', capability='list_snapshot',
                subject=snapshot_id, status='fresh', received_at=now, observed_at=now, expires_at=now+3600000,
                payload={'signature':signature, 'ids':ids}))
        else:
            ids = saved.payload['ids']
        all_total = session.scalar(select(func.count()).select_from(base).where(*conditions))
        cohort = conditions + [base.c.id.in_(ids)]
        total = session.scalar(select(func.count()).select_from(base).where(*cohort))
        pages = max(1, (total+size-1)//size); page = min(page, pages)
        fixed_order = case({key:i for i,key in enumerate(ids)}, value=base.c.id, else_=len(ids)) if ids else base.c.id
        records = session.execute(select(base).where(*cohort).order_by(fixed_order).offset((page-1)*size).limit(size)).mappings()
        rows = []
        for i, r in enumerate(records):
            value = dict(r['payload']); value.update(category=r['category'], action_label=LABELS[r['category']],
                valid_until=r['valid_until'], row_number=(page-1)*size+i+1)
            if r['current_price'] is not None:
                value.update(price=str(r['current_price']), as_of=r['quote_as_of'])
                first = D(str(value.get('first_seen_price') or 0))
                value['since_first_pct'] = str((r['current_price']/first-1)*100) if first>0 else None
                value['targets']={k:({**t,'remaining_upside_pct':str((D(t['low'])/r['current_price']-1)*100)} if t and r['current_price']>0 else t) for k,t in value.get('targets',{}).items()}
            if r['category'] == 'risk' and value['action_code'] in ('participate','trial','waiting','no_chase'):
                value.update(action_code='paused', entry_type=None, reason='行情、计划有效期或保护条件已变化，暂停新买入；原计划保护继续')
            rows.append(value)
        beat = session.get(WorkerHeartbeat, 'signals')
        projection = session.get(WorkerHeartbeat, 'signal_projection')
        counts = dict(session.execute(select(base.c.category, func.count()).where(base.c.rn == 1).group_by(base.c.category)).all())
        counts['history']=session.scalar(select(func.count()).select_from(base).where(base.c.category=='history'))
        return {'as_of':now,'rows':rows,'view':view,'groups':[{'id':k,'label':v,'count':counts.get(k,0)} for k,v in LABELS.items()],
            'scan':{**(beat.details if beat else {}),'as_of':beat.updated_at if beat else None},
            'projection':{'as_of':projection.updated_at if projection else None,'status':'available' if projection else 'initializing',
                'coverage':projection.details.get('coverage',{}) if projection else {}},
            'pagination':{'page':page,'page_size':size,'pages':pages,'total':total,'list_snapshot':snapshot_id,
                'new_assets_waiting':max(0,all_total-total),'ordering':'fixed_order_latest_safety'}}
