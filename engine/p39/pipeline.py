"""Persisted T2 demand, bounded scheduling and exact causal provenance.

No exchange I/O. Source records and result references survive a process restart.
Existing plans without an origin remain legacy_unlinked, never guessed by symbol.
"""
from contextvars import ContextVar
from copy import deepcopy
from hashlib import sha256
import json
import os
import time
from sqlalchemy import select, or_, text, func, BigInteger
from sqlalchemy.dialects.postgresql import insert
from engine.db import Session
from engine.db_retry import retry_transaction
from engine.p39.timing import ORDINARY_MS
from engine.models import (AnalysisDemand, AnalysisResult, MarketAnalysisState,
    Observation, Instrument, Watchlist, Campaign, Episode, Plan, UserPreference, Task)

VERSION = 'p39-layered-analysis-v1'
analysis_context = ContextVar('p39_analysis_context', default=None)
_schema = {'checked': 0, 'ready': False, 'database': None}

def enabled():
    if os.getenv('ERJI_P39_LAYERED_ANALYSIS', '1').lower() in ('0', 'false', 'off'):return False
    # New code may be present before activation/migration. Never query absent
    # tables or silently upgrade production from a reader/collector.
    from engine.db import engine
    from sqlalchemy import inspect
    now = time.monotonic();database=str(engine.url)
    if _schema['database'] != database or now-_schema['checked'] > 10:
        inspector=inspect(engine)
        ready=all(inspector.has_table(name) for name in ('analysis_demands','analysis_results','market_analysis_states'))
        _schema.update(checked=now,ready=ready,database=database)
    return _schema['ready']

def digest(value):
    return sha256(json.dumps(value, sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest()

def demand_id(iid, origin_type, source_id):
    return digest([VERSION, iid, origin_type, source_id])

def observation_source(at, reasons, rule, config, basis, direction, asset_id, received_at=None, trigger_event_id=None, evaluated_at=None):
    return {'source_at':at,'persisted_at':received_at,'received_at':None,'reasons':deepcopy(reasons or []),'rule_version':rule,
        'config_hash':digest(config or {}),'price_basis':deepcopy(basis),'event_direction':direction,'asset_id':asset_id,
        'trigger_event_id':trigger_event_id,'evaluated_at':evaluated_at}

def lock_demands(s,ids):
    """Reserve existing AND new demand keys in one stable order."""
    held=s.info.setdefault('p39_locked_demands',set())
    ids=sorted(set(ids)-held)
    with s.no_autoflush:
        if s.bind.dialect.name=='postgresql':
            if ids:s.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('p39:demand:' || ident,0)) FROM (SELECT unnest(CAST(:ids AS text[])) ident ORDER BY ident) ordered"),{'ids':ids})
        if ids:
            rows=list(s.scalars(select(AnalysisDemand).where(AnalysisDemand.id.in_(ids))
                .order_by(AnalysisDemand.id).with_for_update().execution_options(populate_existing=True)))
            s.info.setdefault('p39_demand_rows',{}).update({r.id:r for r in rows})
    held.update(ids)

def upsert(s, iid, origin_type, source_id, at, expires_at=None, payload=None, state='active'):
    """Only an actual source change renews demand, not selector polling."""
    ident = demand_id(iid, origin_type, source_id)
    # Imported legacy observations may lack a registered native market. Keep
    # their history, but never invent an instrument just to admit analysis.
    if s.get(Instrument,iid) is None:return None
    lock_demands(s,[ident])
    row = s.get(AnalysisDemand, ident)
    value = dict(payload or {})
    if row and (at < row.updated_at or value.get('source_at', at) < row.payload.get('source', {}).get('source_at', 0)):
        return row
    if row and (row.state, row.expires_at, row.payload.get('source')) == (state, expires_at, value):
        return row
    if not row:
        s.execute(insert(AnalysisDemand).values(id=ident, instrument_id=iid,
            origin_type=origin_type, source_id=source_id, state=state, created_at=at,
            updated_at=at, expires_at=expires_at, payload={'source': value})
            .on_conflict_do_nothing(index_elements=['id']))
        row = s.get(AnalysisDemand, ident, populate_existing=True, with_for_update=True)
        if at < row.updated_at:return row
    row.state = state; row.expires_at = expires_at; row.updated_at = at
    row.payload = {**row.payload, 'source': value}
    return row

def live_filter(at):
    return (AnalysisDemand.state == 'active') & or_(AnalysisDemand.expires_at.is_(None), AnalysisDemand.expires_at > at)

def synchronize(s, at, config):
    """Recover known real sources using scalar fields, not large episode JSON."""
    from engine.pd2.universe_policy import primary_eligible
    instruments = {i.id: i for i in s.scalars(select(Instrument)) if primary_eligible(i)}
    seen = set();descriptors={}
    def keep(iid, origin, source, expires=None, data=None):
        if iid not in instruments: return
        ident=demand_id(iid,origin,source)
        descriptors[ident]=(iid,origin,source,expires,data);seen.add(ident)
    for ident, iid, last, status, pinned, manual, reasons, rule, rule_config, basis, direction, asset_id, received, trigger_id, evaluated, trigger_source in s.execute(select(
        Observation.id, Observation.instrument_id, Observation.last_at, Observation.status,
        Observation.payload['pinned'].as_boolean(), Observation.payload['manual'].as_boolean(),Observation.payload['reasons'],
        Observation.payload['rule_version'],Observation.payload['last_rule_config'],Observation.payload['last_basis'],
        Observation.direction,Observation.asset_id,Observation.payload['persisted_at'].as_string().cast(BigInteger),
        Observation.payload['trigger_event_id'].as_string(),Observation.payload['trigger_evaluated_at'].as_string().cast(BigInteger),
        Observation.payload['source_at'].as_string().cast(BigInteger))
        .where(Observation.visible_at.is_not(None), Observation.status != 'archived')):
        if not manual and last + config['cool_ms'] > at:
            keep(iid, 'observation', ident, last + config['cool_ms'], observation_source(last,reasons,rule,rule_config,basis,direction,asset_id,received,trigger_id if trigger_source==last else None,evaluated if trigger_source==last else None))
        if pinned:
            keep(iid, 'manual', ident, data={'observation_id': ident})
    for ident, iid, phase, rule, trigger, boundary, box_boundary, protection, valid, data_status in s.execute(select(
        Episode.id, Campaign.reference_instrument_id, Episode.phase, Episode.strategy_id,
        Episode.evidence['trigger'], Episode.evidence['threshold'],Episode.evidence['box']['trigger_price'], Episode.evidence['protection'],
        Episode.evidence['structure_valid_until'], Episode.evidence['data_status'])
        .join(Campaign).where(Episode.phase.notin_(('ended', 'invalidated')))):
        boundary=boundary if boundary is not None else box_boundary
        if not trigger and boundary is not None:
            # Candidate expiry is a strategy decision, not the display freshness TTL.
            keep(iid, 'candidate', ident, data={'strategy_id': rule, 'threshold': boundary,
                'protection': protection, 'data_status': data_status})
    from engine.p38.plan_queries import live_plans
    for pid, iid, revision, expires in s.execute(live_plans(at, entry=True)):
        keep(iid, 'plan_recovery', pid, expires, {'plan_id': pid, 'revision': revision})
    # End only source-derived demand. Baseline/explicit request state is owned
    # by its producer; retirement never erases observations or frozen evidence.
    existing={r.id:r for r in s.scalars(select(AnalysisDemand).where(AnalysisDemand.origin_type.in_(('observation','manual','candidate','plan_recovery'))))}
    observation_ids=set(s.scalars(select(Observation.id)))
    retiring=[r.id for r in existing.values() if r.state=='active' and r.id not in seen and
        (r.origin_type!='manual' or r.source_id in observation_ids or r.source_id.startswith('watchlist:'))]
    changed=[]
    for ident,(_,_,_,expires,data) in descriptors.items():
        old=existing.get(ident)
        if old and (at<old.updated_at or (old.state,old.expires_at,old.payload.get('source'))==('active',expires,dict(data or {}))):continue
        changed.append(ident)
    # Include retirement in the same reservation order, including new IDs.
    lock_demands(s,set(changed)|set(retiring))
    for ident in sorted(changed):
        iid,origin,source,expires,data=descriptors[ident]
        upsert(s,iid,origin,source,at,expires,data)
    for row in s.scalars(select(AnalysisDemand).where(AnalysisDemand.id.in_(retiring)).order_by(AnalysisDemand.id)):
        if row.id not in seen and row.updated_at<=at:
            row.state = 'ended'; row.updated_at = at
            row.payload = {**row.payload, 'ended_reason': 'source_inactive'}

def sources(s, iid, at):
    return [{'demand_id': d.id, 'origin_type': d.origin_type, 'source_id': d.source_id,
        'created_at': d.created_at, 'source': d.payload.get('source', {})}
        for d in s.scalars(select(AnalysisDemand).where(AnalysisDemand.instrument_id == iid,
            live_filter(at)).order_by(AnalysisDemand.created_at, AnalysisDemand.id))]

def has_demand(s, iid, at):
    return s.scalar(select(AnalysisDemand.id).where(AnalysisDemand.instrument_id == iid, live_filter(at)).limit(1)) is not None

@retry_transaction
def wake_markets(ids, at, reason):
    """Retry real demands on new facts; never create demand from a candle tick."""
    from engine.p38.jobs import KINDS
    ids=sorted(set(ids))
    if not ids or not enabled():return 0
    with Session.begin() as s:
        needed=set(s.scalars(select(AnalysisDemand.instrument_id).where(live_filter(at),AnalysisDemand.instrument_id.in_(ids))))
        for state in s.scalars(select(MarketAnalysisState).where(MarketAnalysisState.instrument_id.in_(needed),
            MarketAnalysisState.work_kind.in_(('trend','structure'))).order_by(MarketAnalysisState.instrument_id,MarketAnalysisState.work_kind).with_for_update()):
            state.next_check_at=at
            state.payload={**state.payload,'wake_at':at,'wake_reason':reason}
        for task in s.scalars(select(Task).where(Task.kind.in_((KINDS['trend'],KINDS['structure'])),
            Task.payload['subject'].as_string().in_(needed),Task.payload['shadow'].as_boolean().is_not(True)).order_by(Task.id).with_for_update()):
            task.payload={**task.payload,'next_review_at':at,'wake_at':at,'wake_reason':reason}
    return len(needed)

def wake_candidates(prices, at):
    """A fresh native price crossing only requests T2's existing confirmation."""
    from decimal import Decimal,InvalidOperation
    crossed=set();fresh_ids=[iid for iid,p in prices.items() if 0<=at-p[0]<=15000]
    if not fresh_ids:return 0
    def side_for(d):
        point=prices.get(d.instrument_id);boundary=d.payload.get('source',{}).get('threshold')
        if not point or not 0<=at-point[0]<=15000 or boundary is None:return None
        try:return Decimal(str(point[1]))>=Decimal(str(boundary)),str(boundary)
        except (InvalidOperation,TypeError,ValueError):return None
    # First inspect without locks; reserve only rows with an actual state change.
    with Session() as s:
        changed=[]
        for d in s.scalars(select(AnalysisDemand).where(live_filter(at),AnalysisDemand.origin_type=='candidate',AnalysisDemand.instrument_id.in_(fresh_ids))):
            point=side_for(d)
            if point and ((d.payload.get('trigger_side'),d.payload.get('trigger_boundary'))!=point or d.payload.get('trigger_wake_pending')):changed.append(d.id)
    @retry_transaction
    def update_chunk(ids):
      found=set()
      with Session.begin() as s:
        lock_demands(s,ids)
        for d in s.scalars(select(AnalysisDemand).where(AnalysisDemand.id.in_(ids),live_filter(at),AnalysisDemand.origin_type=='candidate').order_by(AnalysisDemand.id)):
            point=side_for(d)
            if point is None:continue
            side,boundary=point
            old=d.payload.get('trigger_side');old_boundary=d.payload.get('trigger_boundary')
            crossing=(old is None and side) or (old is not None and side!=old) or (old_boundary!=str(boundary) and side)
            pending=bool(d.payload.get('trigger_wake_pending') or crossing)
            if pending:found.add(d.instrument_id)
            if old!=side or old_boundary!=str(boundary):
                d.payload={**d.payload,'trigger_side':side,'trigger_boundary':str(boundary),'trigger_checked_at':at,'trigger_wake_pending':pending}
      return found
    changed.sort()
    for offset in range(0,len(changed),64):crossed.update(update_chunk(changed[offset:offset+64]))
    result=wake_markets(crossed,at,'candidate_price_crossing')
    @retry_transaction
    def acknowledged(ids):
        with Session.begin() as s:
            lock_demands(s,ids)
            for d in s.scalars(select(AnalysisDemand).where(AnalysisDemand.id.in_(ids))):
                if d.instrument_id in crossed and d.payload.get('trigger_checked_at',0)<=at and d.payload.get('trigger_wake_pending'):
                    d.payload={**d.payload,'trigger_wake_pending':False}
    for offset in range(0,len(changed),64):acknowledged(changed[offset:offset+64])
    return result

@retry_transaction
def recover_demands(at, config):
    with Session.begin() as s:
        s.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'), {'k': 'p39:demand_recovery'})
        checkpoint=s.get(UserPreference,'p39_demand_sync')
        if checkpoint and 0<=at-checkpoint.updated_at<ORDINARY_MS:return
        synchronize(s,at,config)
        s.merge(UserPreference(key='p39_demand_sync',updated_at=at,payload={'version':VERSION}))


def compute_filter(at):
    # Dormant strategy candidates are monitored cheaply; only an actual
    # threshold crossing requests renewed analysis. Explicit manual requests
    # remain valid without making the entire watchlist a hot collection tier.
    return live_filter(at) & or_(AnalysisDemand.origin_type != 'candidate',
        AnalysisDemand.payload['trigger_side'].as_boolean().is_(True))

@retry_transaction
def select_work(at, capacity, kind, config):
    """Slots are native markets, not display representatives or execution threads."""
    from engine.p38.jobs import KINDS
    from engine.repository import emit
    recover_demands(at,config)
    with Session.begin() as s:
        s.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'), {'k': 'p39:demand_selector'})
        demands = list(s.scalars(select(AnalysisDemand).where(compute_filter(at))))
        grouped = {}
        for d in demands: grouped.setdefault(d.instrument_id, []).append(d)
        states = {r.instrument_id: r for r in s.scalars(select(MarketAnalysisState).where(
            MarketAnalysisState.work_kind == kind, MarketAnalysisState.instrument_id.in_(grouped)))}
        tasks = {p.get('subject'): (state, p) for state, p in s.execute(select(Task.state, Task.payload)
            .where(Task.kind == KINDS[kind], Task.payload['shadow'].as_boolean().is_not(True),Task.payload['subject'].as_string().in_(grouped)))}
        key = 'p39_' + kind + '_active'; old = s.get(UserPreference, key)
        previous = old.payload if old else {}; admissions = previous.get('admitted_at', {})
        def ordering(iid):
            state = states.get(iid); origins = grouped[iid]
            # Oldest unserved/reviewed demand gets service; no arbitrary percentage.
            reviewed = max(state.last_attempt_at if state else 0, admissions.get(iid, 0))
            source_at = min(d.created_at for d in origins)
            held = iid in previous.get('instrument_ids', []) and tasks.get(iid, ('', {}))[0] in ('queued', 'leased') and not tasks.get(iid,('',{}))[1].get('retired_reason')
            first_manual=not reviewed and any(d.origin_type=='manual' for d in origins)
            return (not held, reviewed if reviewed else source_at - 10**15, not first_manual, source_at, iid)
        due = [iid for iid in grouped if not states.get(iid) or
            states[iid].analysis_status not in ('waiting_inputs', 'failed') or states[iid].next_check_at <= at or
            any(d.updated_at > states[iid].last_attempt_at for d in grouped[iid])]
        chosen = sorted(due, key=ordering)[:max(0, capacity)]
        admissions = {iid: admissions.get(iid, 0) for iid in grouped}
        for iid in chosen:
            if tasks.get(iid, ('', {}))[0] not in ('queued', 'leased'): admissions[iid] = at
        value = {'instrument_ids': chosen, 'admitted_at': admissions,
            'source': VERSION, 'demand_markets': len(grouped), 'waiting_input_markets': len(grouped) - len(due)}
        if previous!=value:s.merge(UserPreference(key=key, updated_at=at, payload=value))
        other = s.get(UserPreference, 'p39_' + ('trend' if kind == 'structure' else 'structure') + '_active')
        union = set(chosen) | set((other.payload if other else {}).get('instrument_ids', []))
        active=s.get(UserPreference,'p37_active')
        active_value={'instrument_ids':sorted(union),'source':VERSION}
        if not active or active.payload!=active_value:s.merge(UserPreference(key='p37_active',updated_at=at,payload=active_value))
        changed = set(previous.get('instrument_ids', [])) ^ set(chosen)
        if changed: emit(s, 'observation_update', {'instruments': sorted(changed)})
        return [(min(grouped[iid], key=lambda d: (d.created_at, d.id)).source_id, iid) for iid in chosen], 0

def origin(context=None):
    context = context or analysis_context.get()
    if not context: return {'origin_type': 'legacy_unlinked', 'pipeline_tier': 'T2'}
    source = context.get('sources', [])
    kinds = sorted({r['origin_type'] for r in source})
    return {'pipeline_tier': 'T2', 'origin_type': kinds[0] if len(kinds) == 1 else 'multiple' if kinds else 'legacy_unlinked',
        'origin_observation_ids': sorted({r['source_id'] for r in source if r['origin_type'] == 'observation'}),
        'demand_ids': [r['demand_id'] for r in source], 'sources': deepcopy(source),
        'analysis_result_id': context['result_id'], 'analysis_market_id': context['instrument_id'],
        'input_version': context['input_version'], 'input_manifest': deepcopy(context['input_manifest']),
        'algorithm_version': VERSION}

@retry_transaction
def begin_result(iid, kind, version, manifest, at, source_refs=None):
    with Session.begin() as s:
        refs = sources(s, iid, at) if source_refs is None else source_refs
        result_id = digest([iid, kind, version, at, refs])
        context = {'instrument_id': iid, 'work_kind': kind, 'result_id': result_id,
            'input_version': version, 'input_manifest': manifest, 'sources': refs}
        # A crash leaves an honest interrupted result, never fabricated completion.
        s.add(AnalysisResult(id=result_id, instrument_id=iid, work_kind=kind,
            input_version=version, computed_at=at, payload={'status': 'started','started_at':at, 'origin': origin(context)}))
        old = s.get(MarketAnalysisState, (iid, kind))
        s.merge(MarketAnalysisState(instrument_id=iid, work_kind=kind, input_version=version,
            result_id=old.result_id if old else None, analysis_status='running', strategy_result=old.strategy_result if old else 'not_analyzed',
            last_attempt_at=at, last_success_at=old.last_success_at if old else None, next_check_at=at,
            payload={**(old.payload if old else {}), 'running_result_id': result_id}))
    return context

@retry_transaction
def complete_result(context, summary, at, error=None):
    with Session.begin() as s:
        row = s.get(AnalysisResult, context['result_id'], with_for_update=True)
        if not row or row.payload.get('status') != 'started': return
        waiting = bool((summary or {}).get('waiting_inputs'))
        superseded=error in ('analysis_inputs_superseded','analysis_source_changed','plan_inputs_superseded')
        deferred=error=='owner_still_running'
        status = 'superseded' if superseded else 'deferred' if deferred else 'failed' if error else 'waiting_inputs' if waiting else 'completed'
        strategy = (summary or {}).get('strategy_result', 'not_applicable' if context['work_kind'] == 'trend' else 'no_match')
        row.payload = {**row.payload, 'status': status, 'finished_at': at, 'summary': summary or {}, 'error': error}
        if not error:row.computed_at=(summary or {}).get('computed_at') or at
        state = s.get(MarketAnalysisState, (context['instrument_id'], context['work_kind']), with_for_update=True)
        if state.payload.get('running_result_id') != row.id: return
        state.analysis_status = status
        if not error:state.strategy_result = strategy
        state.next_check_at = at + (250 if superseded or deferred else 120000 if waiting or error else 600000)
        if not error:
            state.input_version = context['input_version']; state.result_id = row.id; state.last_success_at = at
        state.payload = {**state.payload, 'summary': summary or {}, 'error': error, 'origin': origin(context)}
        lock_demands(s,[ref['demand_id'] for ref in context['sources']])
        for ref in sorted(context['sources'],key=lambda ref:ref['demand_id']):
            d = s.get(AnalysisDemand, ref['demand_id'])
            if d: d.payload = {**d.payload, context['work_kind'] + '_result_id': row.id}
        from engine.repository import emit
        emit(s, 'observation_update', {'instruments': [context['instrument_id']]})

@retry_transaction
def baseline_tick(at, batch_size=1):
    """Bounded shadow rotation. It cannot publish or compete with live T2 tasks."""
    from engine.pd2.universe_policy import primary_eligible
    with Session.begin() as s:
        key = 'p39_baseline_screen'; old = s.get(UserPreference, key, with_for_update=True)
        previous = old.payload if old else {}
        if at < previous.get('next_at', 0): return []
        ids = sorted(i.id for i in s.scalars(select(Instrument).where(Instrument.venue.in_(('binance', 'binance_spot')),
            Instrument.quote_asset == 'USDT')) if primary_eligible(i))
        after = [iid for iid in ids if iid > previous.get('cursor', '')]
        chosen = (after or ids)[:min(max(batch_size, 0), 2)]
        lock_demands(s,[demand_id(iid,'baseline_screen',iid) for iid in chosen])
        for iid in sorted(chosen,key=lambda iid:demand_id(iid,'baseline_screen',iid)):
            demand=upsert(s, iid, 'baseline_screen', iid, at, at + 3600000,
                {'shadow': True, 'reason': 'bounded_rotation'}, state='shadow')
            demand.payload={k:v for k,v in demand.payload.items() if k not in ('checked_at','reused_task_id')}
        s.merge(UserPreference(key=key, updated_at=at, payload={'cursor': chosen[-1] if chosen else '',
            'next_at': at + 300000, 'mode': 'shadow', 'batch_size': len(chosen), 'universe': len(ids)}))
        return chosen

def submit_shadow(at):
    """Reuse the structure lane only while its live work is idle."""
    from engine.p38.jobs import KINDS,dependency_versions,identity
    with Session() as s:
        pending=bool(s.scalar(select(Task.id).where(Task.kind==KINDS['structure'],
            Task.state.in_(('queued','leased','failed')),Task.payload['shadow'].as_boolean().is_not(True)).limit(1)))
        if pending:return 0
        live_market=select(AnalysisDemand.instrument_id).where(live_filter(at))
        demand=s.scalar(select(AnalysisDemand).where(AnalysisDemand.state=='shadow',AnalysisDemand.expires_at>at,AnalysisDemand.instrument_id.notin_(live_market),
            AnalysisDemand.payload['checked_at'].as_string().cast(BigInteger).is_(None)).order_by(AnalysisDemand.created_at).limit(1))
        if not demand or has_demand(s,demand.instrument_id,at):return 0
        iid=demand.instrument_id;did=demand.id
    version=dependency_versions([iid],at,'structure')[iid]
    key=identity('baseline_shadow:'+iid,'structure')
    with Session.begin() as s:
        s.execute(insert(Task).values(id=key,dedupe_key=key,kind=KINDS['structure'],state='queued',available_at=at,attempts=0,
            payload={'subject':iid,'work_kind':'structure','shadow':True,'demand_id':did,'desired_version':version,'submitted_at':at})
            .on_conflict_do_nothing(index_elements=['id']))
        row=s.get(Task,key,with_for_update=True)
        if row.payload.get('retired_reason') or row.payload.get('desired_version')!=version or (row.state=='complete' and row.payload.get('completed_version')!=version):
            row.payload={k:v for k,v in row.payload.items() if k!='retired_reason'}
            row.payload={**row.payload,'desired_version':version,'submitted_at':at,'demand_id':did}
            if row.state!='leased':row.state='queued';row.available_at=at
        elif row.state=='complete':
            d=s.get(AnalysisDemand,did);d.payload={**d.payload,'checked_at':at,'reused_task_id':key}
    return 1

def analyze_shadow(iid,version,did,at):
    """Same recognizers, closed native facts, no candidates/plans/projection writes."""
    from engine.p38.windows import read,token
    from engine.strategies.p33 import evaluate as patterns
    from engine.strategies.structures import candidate as boxes
    from engine.repository import runtime_config
    from engine.pd2.analysis import technical
    from decimal import Decimal
    frames=('60','240','1D','1W');cfg=runtime_config()
    with Session() as s:
        if has_demand(s,iid,at):return {'retired':True}
        inst=s.get(Instrument,iid);d=s.get(AnalysisDemand,did)
        if not inst or not d or d.state!='shadow' or (d.expires_at or 0)<=at:return {'retired':True}
        refs=[{'demand_id':d.id,'origin_type':d.origin_type,'source_id':d.source_id,'created_at':d.created_at,'source':d.payload.get('source',{})}]
        manifest={tf:token(s,iid,tf,at,limit=300) for tf in frames}
        bars={tf:[{k:str(v) if isinstance(v,Decimal) else v for k,v in b.items()} for b in read(s,iid,tf,at,300)] for tf in frames}
        spec=deepcopy(inst.spec)
    context=begin_result(iid,'baseline_screen',version,manifest,at,refs)
    try:
        summaries={tf:technical(bars[tf],tf,at,spec.get('tick_size') or '0.00000001',cfg.analysis) for tf in frames}
        result=patterns(bars,at,spec['tick_size'],iid) if spec.get('tick_size') else {'results':[],'error':'missing_tick_size'}
        box=boxes(iid,bars['240'],bars['60'],at)
        outcomes=[{'strategy_id':r.get('strategy_id'),'status':r.get('status'),'event_id':r.get('event_id'),
            'reason':r.get('reason'),'trigger':r.get('trigger')} for r in result.get('results',[]) if r.get('strategy_id') not in cfg.disabled_strategies]
        summary={'shadow':True,'pattern_results':outcomes,'box_phase':box.get('phase') if box else None,
            'timeframes':{tf:{k:v.get(k) for k in ('status','closed_at','bar_count','gap_count')} for tf,v in summaries.items()},
            'waiting_inputs':bool(result.get('error') or any(r.get('status')=='missing_data' for r in outcomes)),
            'strategy_result':'shadow_only','publication':False}
        with Session() as s:
            from engine.adapters.base import now_ms
            checked_at=max(at,now_ms())
            if any(token(s,iid,tf,checked_at,limit=300)!=v for tf,v in manifest.items()):raise RuntimeError('analysis_source_changed')
        complete_result(context,summary,at)
        with Session.begin() as s:
            d=s.get(AnalysisDemand,did,with_for_update=True)
            d.payload={**d.payload,'checked_at':at,'result_id':context['result_id']}
        return summary
    except Exception as error:
        complete_result(context,{},at,type(error).__name__);raise

def capacity_metrics(at,window_ms=1200000):
    """Independent market/version completions, never scanner log throughput."""
    with Session() as s:
        origins={origin:count for origin,count in s.execute(select(AnalysisDemand.origin_type,func.count()).where(
            live_filter(at)).group_by(AnalysisDemand.origin_type))}
        active=s.scalar(select(func.count(func.distinct(AnalysisDemand.instrument_id))).where(compute_filter(at))) or 0
        from engine.p39.priority import market_tiers
        t2,t3,_=market_tiers(s,at)
        source_at=AnalysisDemand.payload['source']['source_at'].as_string().cast(BigInteger)
        arrivals=s.scalar(select(func.count(func.distinct(AnalysisDemand.instrument_id))).where(
            source_at>=at-window_ms,source_at<=at,AnalysisDemand.origin_type=='observation')) or 0
        rows=list(s.execute(select(AnalysisResult.instrument_id,AnalysisResult.work_kind,AnalysisResult.input_version,
            AnalysisResult.payload['started_at'].as_string().cast(BigInteger),AnalysisResult.payload['finished_at'].as_string().cast(BigInteger),AnalysisResult.payload['status'].as_string())
            .where(AnalysisResult.computed_at>=at-window_ms,AnalysisResult.computed_at<=at,
                AnalysisResult.payload['finished_at'].as_string().cast(BigInteger)<=at,AnalysisResult.work_kind.in_(('trend','structure')))))
    completed={};service={}
    for iid,kind,version,started,finished,status in rows:
        if status=='completed':
            completed.setdefault(kind,set()).add((iid,version))
            if finished is not None and started is not None:service.setdefault(kind,[]).append(max(0,finished-started))
    return {'window_ms':window_ms,'active_native_markets':active,'t2_native_markets':len(t2),
        't3_native_markets':len(t3),'by_origin':origins,
        'new_demand_native_markets':arrivals,'arrival_markets_per_minute':round(arrivals*60000/window_ms,3),
        'independent_market_input_completions':{k:len(v) for k,v in completed.items()},
        'service_ms':{k:{'samples':len(v),'mean':round(sum(v)/len(v),1),'p95':sorted(v)[min(len(v)-1,int(len(v)*.95))]} for k,v in service.items()},
        'scope':'distinct native market/input version; arrivals use actual observation source_at; recovery, repeat reviews and shadow excluded'}
