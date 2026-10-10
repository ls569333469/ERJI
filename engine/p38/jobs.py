"""Durable latest-version jobs. Submitting never steals an executing lease.

Task rows are coordination state, not user preferences or historical evidence.
The session advisory fence additionally prevents an expired lease from running
alongside a still alive owner. Process death releases that fence automatically.
"""
from hashlib import sha256
from uuid import uuid4
import logging
from sqlalchemy import select,text,func,case
from sqlalchemy.dialects.postgresql import insert
from engine.db import Session,engine
from engine.models import Task,Candle,MarketState,Observation,SettingsVersion,UserPreference,Instrument
from engine.adapters.base import now_ms

DEFERRED={'analysis_inputs_superseded','analysis_source_changed','plan_inputs_superseded','owner_still_running'}

KINDS={k:'p39_'+k for k in ('trend','structure','entry','protect','projection')}
KIND=KINDS['structure']
from engine.p39.timing import ANALYSIS_SLOTS as LANE_LIMITS
LANES={'protect':('protect',),'entry':('entry',),'projection':('projection',),'trend':('trend',),'structure':('structure',)}

def identity(subject,work_kind='structure'):return sha256((KINDS[work_kind]+':'+subject).encode()).hexdigest()

def dependency_versions(ids,at=None,work_kind='structure'):
    at=now_ms() if at is None else at
    with Session() as s:
        frames=('5','60','240','1D') if work_kind=='trend' else ('60','240','1D','1W')
        from engine.p38.windows import batch_tokens
        from engine.p37.analysis import SOURCE_5M_BARS
        from engine.pd2.structural_history import LIMITS
        candles=batch_tokens(s,ids,frames,at,LIMITS if work_kind=='structure' else {'5':SOURCE_5M_BARS}) if work_kind in ('trend','structure','entry','protect') else {}
        states={iid:(stamp,not 0<=at-stamp<=90000) for iid,stamp in s.execute(select(MarketState.instrument_id,MarketState.as_of).where(MarketState.instrument_id.in_(ids)))} if work_kind in ('entry','protect') else {}
        # Calculation versions depend on actual facts. Retry/freshness has its
        # own next_review_at and cannot cancel unchanged in-flight inputs.
        specs={i.id:i.spec for i in s.scalars(select(Instrument).where(Instrument.id.in_(ids)))} if work_kind in ('structure','trend') else {}
        from engine.models import Plan,DataCapability
        plans={iid:[] for iid in ids}
        if work_kind in ('entry','protect','projection'):
            for pid,iid,revision,expires in s.execute(select(Plan.id,Plan.execution_instrument_id,Plan.revision,Plan.expires_at).where(Plan.execution_instrument_id.in_(ids))):plans[iid].append((pid,revision,at>=expires))
        books={iid:(ref,status,at>=expires) for iid,ref,status,expires in s.execute(select(DataCapability.subject,DataCapability.raw_ref,DataCapability.status,DataCapability.expires_at).where(DataCapability.capability=='execution_book',DataCapability.subject.in_(ids)))} if work_kind=='entry' else {}
        if work_kind=='projection':
            from engine.models import SignalSnapshot,CurrentSignal
            from engine.p38.plan_queries import latest_ids
            published={r.id:r.payload.get('snapshot_id') for r in s.scalars(select(CurrentSignal).where(CurrentSignal.instrument_id.in_(ids)))}
            latest={iid:[] for iid in ids}
            for iid,pid,sid,stamp in s.execute(select(Plan.execution_instrument_id,SignalSnapshot.plan_id,SignalSnapshot.id,SignalSnapshot.as_of).join(Plan).where(SignalSnapshot.id.in_(latest_ids(ids)))):
                if published.get(pid)!=sid:latest[iid].append((pid,sid,stamp))
            # Reconcile immediately after a changed decision (including a process
            # dying between snapshot commit and publish). Successfully published
            # fast decisions need no duplicate heavy projection; ordinary full
            # projection reconciles candidates every five minutes.
            from engine.p39.timing import scheduled_at,ORDINARY_MS
            states={iid:(sorted(latest[iid]),scheduled_at(iid,at,ORDINARY_MS)) for iid in ids}
        config=s.scalar(select(func.max(SettingsVersion.id)).where(SettingsVersion.state=='published')) or 0
        anomaly=s.get(UserPreference,'p37_config');anomaly_at=anomaly.updated_at if anomaly else 0
    from engine.pd2.decision import COST_POLICY
    from engine.p39.pipeline import VERSION,digest
    return {iid:digest((candles.get(iid),states.get(iid),books.get(iid),sorted(plans[iid]),specs.get(iid),config,anomaly_at,COST_POLICY,VERSION)) for iid in ids}

def eligible(ids,work_kind='structure'):
    with Session() as s:
        active=s.get(UserPreference,'p39_'+work_kind+'_active') or s.get(UserPreference,'p37_active');chosen=set((active.payload if active else {}).get('instrument_ids',[]))
        from engine.p39.pipeline import enabled, compute_filter
        if enabled() and work_kind in ('trend','structure'):
            from engine.models import AnalysisDemand
            needed=set(s.scalars(select(AnalysisDemand.instrument_id).where(compute_filter(now_ms()),AnalysisDemand.instrument_id.in_(ids))))
            chosen &= needed
        # Managed plans use entry/protect/projection independently; they cannot
        # bypass the observation selector's bounded deep-analysis slots.
    return sorted(set(ids)&chosen)

def submit(ids,reason='repair',at=None,work_kind='structure'):
    ids=sorted(set(ids));at=now_ms() if at is None else at
    if work_kind in ('trend','structure') and reason!='repair':ids=eligible(ids,work_kind)
    elif reason=='projection_repair':ids=sorted(set(ids)&set(managed_targets(at)['projection']))
    versions=dependency_versions(ids,at,work_kind) if ids else {}
    ordered=sorted((identity(iid,work_kind),iid) for iid in ids)
    with Session() as s:
        existing={ident:payload for ident,payload in s.execute(select(Task.id,Task.payload).where(Task.id.in_([i for i,_ in ordered])))} if ordered else {}
    def review_due(p):return work_kind in ('trend','structure') and p.get('completed_version')==p.get('desired_version') and at>=p.get('next_review_at',at+1)
    changed=[(ident,iid) for ident,iid in ordered if existing.get(ident,{}).get('retired_reason') or review_due(existing.get(ident,{})) or versions[iid] not in (
        existing.get(ident,{}).get('completed_version'),existing.get(ident,{}).get('desired_version'))]
    # Lock/update only changed rows, in bounded sorted batches. Concurrent
    # producers cannot steal a lease; skipped locked rows retry next iteration.
    for start in range(0,len(changed),32):
        chunk=changed[start:start+32]
        with Session.begin() as s:
            missing=[dict(id=ident,dedupe_key=ident,kind=KINDS[work_kind],state='queued',available_at=at,attempts=0,payload={'subject':iid,'work_kind':work_kind}) for ident,iid in chunk if ident not in existing]
            if missing:s.execute(insert(Task).values(missing).on_conflict_do_nothing(index_elements=['id']))
            subjects=dict(chunk)
            for row in s.scalars(select(Task).where(Task.id.in_(subjects)).order_by(Task.id).with_for_update(skip_locked=True)):
                p=dict(row.payload);version=versions[subjects[row.id]]
                if not p.get('retired_reason') and not review_due(p) and version in (p.get('completed_version'),p.get('desired_version')):continue
                if row.state=='complete':p['first_pending_at']=at
                elif not p.get('first_pending_at'):p['first_pending_at']=min(at,row.available_at,p.get('submitted_at',at))
                p.update(desired_version=version,reason=reason,submitted_at=at,latest_desired_at=at);p.pop('retired_reason',None)
                p.pop('deferred_reason',None)
                row.payload=p
                if row.state!='leased':
                    if row.state=='complete':row.available_at=at
                    row.state='queued'
    return len(ids)

def take(at=None,ttl=120000,work_kinds=None,exclude_subjects=()):
    at=now_ms() if at is None else at
    with Session.begin() as s:
        query=select(Task).where(Task.kind.in_([KINDS[k] for k in work_kinds] if work_kinds is not None else KINDS.values()),Task.available_at<=at,
            (Task.state.in_(['queued','failed']))|((Task.state=='leased')&(Task.lease_until<=at)))
        if exclude_subjects:query=query.where(Task.payload['subject'].as_string().notin_(exclude_subjects))
        if work_kinds is not None and set(work_kinds)=={'projection'}:
            from sqlalchemy.orm import aliased
            urgent=aliased(Task)
            query=query.where(~select(urgent.id).where(urgent.kind.in_([KINDS['entry'],KINDS['protect']]),
                urgent.payload['subject'].as_string()==Task.payload['subject'].as_string(),
                urgent.state.in_(['queued','leased','failed']),urgent.available_at<=at).exists())
        row=s.scalar(query.order_by(
                case((Task.kind==KINDS['protect'],1),(Task.kind==KINDS['entry'],2),(Task.kind==KINDS['projection'],3),(Task.kind==KINDS['structure'],4),else_=5),
                case((Task.payload['shadow'].as_boolean().is_(True),1),else_=0),
                Task.available_at,Task.id).with_for_update(skip_locked=True).limit(1))
        if not row:return None
        owner=uuid4().hex;p=dict(row.payload);p['running_version']=p['desired_version']
        p.update(started_at=at,queue_wait_ms=max(0,at-p.get('first_pending_at',row.available_at)),
            available_wait_ms=max(0,at-row.available_at),desired_wait_ms=max(0,at-p.get('latest_desired_at',p.get('submitted_at',row.available_at))))
        row.payload=p;row.state='leased';row.lease_owner=owner;row.lease_until=at+ttl;row.attempts+=1
        return {'id':row.id,'owner':owner,'instrument_id':p['subject'],'version':p['running_version'],'work_kind':p.get('work_kind','structure'),
            'shadow':bool(p.get('shadow')),'demand_id':p.get('demand_id'),
            'projection_cycle':p.get('projection_cycle')}

def renew(lease,at=None):
    with Session.begin() as s:
        row=s.get(Task,lease['id'],with_for_update=True)
        if not row or row.state!='leased' or row.lease_owner!=lease['owner'] or row.lease_until<(now_ms() if at is None else at):return False
        row.lease_until=(now_ms() if at is None else at)+120000;return True

def finish(lease,error=None,at=None,retired_reason=None):
    at=now_ms() if at is None else at
    deferred=error in DEFERRED
    with Session.begin() as s:
        row=s.get(Task,lease['id'],with_for_update=True)
        if not row or row.state!='leased' or row.lease_owner!=lease['owner'] or row.lease_until<at:return False
        p=dict(row.payload);p.update(finished_at=at,error=error,
            execution_ms=max(0,at-p.get('started_at',at)),
            completed_count=p.get('completed_count',0)+(0 if error or retired_reason else 1),
            failed_count=p.get('failed_count',0)+(1 if error and not deferred else 0),
            deferred_count=p.get('deferred_count',0)+(1 if deferred else 0))
        if retired_reason:
            row.state='complete';p['retired_reason']=retired_reason;p.pop('deferred_reason',None);p.pop('first_pending_at',None)
        elif deferred:
            row.state='queued';row.available_at=at+250;p.update(error=None,deferred_reason=error)
        elif error:row.state='failed';row.available_at=at+min(60000,1000*2**min(row.attempts,6))
        else:
            p.pop('deferred_reason',None)
            p['completed_version']=lease['version']
            row.state='queued' if p['desired_version']!=lease['version'] else 'complete';row.available_at=at
            if row.state=='complete':p.pop('first_pending_at',None)
            if lease.get('completed_projection_cycle') is not None:p['projection_cycle']=lease['completed_projection_cycle']
            if lease.get('work_kind','structure') in ('trend','structure'):
                p['next_review_at']=at+600000
                from engine.p39.pipeline import enabled
                if enabled() and not lease.get('shadow'):
                    from engine.models import MarketAnalysisState
                    state=s.get(MarketAnalysisState,(lease['instrument_id'],lease.get('work_kind','structure')))
                    if state and state.analysis_status=='waiting_inputs':p['next_review_at']=state.next_check_at
        row.payload=p;row.lease_owner=None;row.lease_until=None
        if lease.get('work_kind') in ('trend','structure'):
            from engine.repository import emit
            emit(s,'observation_update',{'instruments':[lease['instrument_id']]})
        return True

async def execute(lease,adapter):
    import asyncio
    from engine.repository import heartbeat,runtime_config
    from engine.models import Instrument
    from engine.pd2.universe_policy import primary_eligible
    from engine.p38.lifecycle import thread,commit_guard
    iid=lease['instrument_id'];kind=lease.get('work_kind','structure');key='p38_execute:'+('trend:' if kind=='trend' else '')+iid;stage='admission';context=None;context_token=None
    if kind in ('trend','structure') and not lease.get('shadow') and iid not in await thread(eligible,[iid],kind):
        await asyncio.to_thread(finish,lease,retired_reason='outside_active_slots');return
    if kind=='entry':
        from engine.p38.plan_queries import live_plans
        def entry_live():
            with Session() as s:return bool(s.execute(live_plans(now_ms(),entry=True,instrument_ids=[iid]).limit(1)).first())
        if not await thread(entry_live):
            await asyncio.to_thread(finish,lease);return
    # Hold the mutation fence per committed work stage, never across HTTP,
    # unrelated reads or the entire chain of structure calculations.
    from engine.p38.fences import borrow
    connection=None;locked=False;keeper=None
    try:
        async def keep():
            while True:
                await asyncio.sleep(20)
                if not await asyncio.to_thread(renew,lease):raise RuntimeError('analysis_lease_lost')
        keeper=asyncio.create_task(keep())
        def instrument():
            with Session() as s:
                inst=s.get(Instrument,iid)
                return inst
        inst=await thread(instrument)
        expected_inputs=None
        def source_inputs(s):
            from engine.p38.windows import batch_tokens
            from engine.p37.analysis import SOURCE_5M_BARS
            from engine.pd2.structural_history import LIMITS
            frames=('5','60','240','1D') if kind=='trend' else ('60','240','1D','1W') if kind in ('structure','entry','protect') else ()
            limits=LIMITS if kind=='structure' else {'5':SOURCE_5M_BARS} if kind=='trend' else {}
            candles=batch_tokens(s,[iid],frames,now_ms(),limits).get(iid,()) if frames else ()
            config=s.scalar(select(func.max(SettingsVersion.id)).where(SettingsVersion.state=='published'))
            anomaly=s.get(UserPreference,'p37_config')
            return candles,config,anomaly.updated_at if anomaly else 0
        def guard(s):
            row=s.get(Task,lease['id'],populate_existing=True,with_for_update=True)
            if not row or row.state!='leased' or row.lease_owner!=lease['owner'] or row.lease_until<now_ms():raise RuntimeError('analysis_lease_lost')
            # New quotes coalesce into the next fast job, rather than cancel
            # every in-flight decision. Its actual quote/book expiry is still
            # enforced by refresh_current, publish and the safe API read.
            if kind in ('trend','structure') and row.payload.get('desired_version')!=lease['version']:raise RuntimeError('analysis_inputs_superseded')
            if expected_inputs is not None and source_inputs(s)!=expected_inputs:raise RuntimeError('analysis_source_changed')
        async def work(fn,*args,**kwargs):
            nonlocal stage,locked,connection
            stage=fn.__name__
            connection=await borrow(kind)
            token=None
            def checked_work():
                nonlocal expected_inputs
                with Session() as s:
                    guard(s)
                    if expected_inputs is None:expected_inputs=source_inputs(s)
                value=fn(*args,**kwargs)
                with Session() as s:guard(s)
                if value and isinstance(value,dict) and value.get('superseded'):raise RuntimeError('analysis_inputs_superseded')
                return value
            try:
                locked=await thread(lambda:connection.execute(text('SELECT pg_try_advisory_lock(hashtext(current_database() || :k))'),{'k':key}).scalar())
                if not locked:raise RuntimeError('owner_still_running')
                token=commit_guard.set(guard)
                from engine.p40.database import measure
                def measured():
                    with measure('mutation_stage',kind+':'+stage):return checked_work()
                return await thread(measured)
            finally:
                if token is not None:commit_guard.reset(token)
                try:
                    if locked:await thread(lambda:connection.execute(text('SELECT pg_advisory_unlock(hashtext(current_database() || :k))'),{'k':key}))
                finally:
                    await thread(connection.close);connection=None;locked=False
        if not inst or (kind in ('trend','structure') and not lease.get('shadow') and iid not in await thread(eligible,[iid],kind)):
            await asyncio.to_thread(finish,lease,retired_reason='instrument_missing' if not inst else 'outside_active_slots');return
        if inst:
            if lease.get('shadow'):
                from engine.p39.pipeline import analyze_shadow
                shadow=await work(analyze_shadow,iid,lease['version'],lease['demand_id'],now_ms())
                await asyncio.to_thread(finish,lease,retired_reason='shadow_demand_inactive' if shadow.get('retired') else None);return
            if kind=='entry' and primary_eligible(inst) and (await thread(runtime_config)).collector_enabled:
                stage='collect_execution_book'
                if await thread(entry_live):
                    io_key='p38_collect_execution:'+iid
                    connection=await borrow(kind);io_locked=False
                    try:
                        io_locked=await thread(lambda:connection.execute(text('SELECT pg_try_advisory_lock(hashtext(current_database() || :k))'),{'k':io_key}).scalar())
                        if not io_locked:raise RuntimeError('owner_still_running')
                        from engine.p40.database import measure
                        with measure('execution_book_http',kind):await adapter.collect(iid)
                    finally:
                        try:
                            if io_locked:await thread(lambda:connection.execute(text('SELECT pg_advisory_unlock(hashtext(current_database() || :k))'),{'k':io_key}))
                        finally:await thread(connection.close);connection=None
            if not await thread(renew,lease):raise RuntimeError('analysis_lease_lost')
            if kind=='entry':
                from engine.pd2.market_selection import select_main
                await work(select_main,inst.asset_id,await thread(runtime_config))
            if kind=='trend':
                from engine.p37.analysis import analyze_market
                from engine.p39.pipeline import begin_result,analysis_context,complete_result,enabled
                if enabled():
                    def begin_trend():
                        return begin_result(iid,kind,lease['version'],expected_inputs,now_ms())
                    context=await work(begin_trend);context_token=analysis_context.set(context)
                analyzed=await work(analyze_market,iid,now_ms())
                if context:await work(complete_result,context,analyzed if isinstance(analyzed,dict) else {'updated':bool(analyzed)},now_ms())
            from engine.signal_service import scan_signals
            from engine.pd2.service import refresh_plans,refresh_current,decision_list
            if kind=='structure':
                from engine.p39.pipeline import begin_result,analysis_context,complete_result,enabled
                if enabled():
                    def begin_structure():
                        return begin_result(iid,kind,lease['version'],expected_inputs,now_ms())
                    context=await work(begin_structure);context_token=analysis_context.set(context)
                from engine.signal_service import prepare_scan
                from engine.p40.database import measure
                def preparation():
                    with measure('structure_prepare','structure'):return prepare_scan([iid])
                stage='prepare_scan'
                prepared_scan=await thread(preparation)
                scanned=await work(scan_signals,[iid],prepared=prepared_scan)
                if scanned and scanned.get('superseded'):raise RuntimeError('analysis_inputs_superseded')
                planned=await work(refresh_plans,[iid],new_only=True)
                if planned and planned.get('superseded'):raise RuntimeError('plan_inputs_superseded')
                def structure_checked():
                    from engine.repository import emit
                    with Session.begin() as s:
                        for row in s.scalars(select(Observation).where(Observation.instrument_id==iid,Observation.visible_at.is_not(None),Observation.status!='archived').with_for_update()):
                            # A successfully consumed task is not proof that
                            # this market passed scanner admission.
                            reason='checked' if (scanned or {}).get('scanned',0)>0 else 'quote_stale' if (scanned or {}).get('quote_stale') else 'candles_stale' if (scanned or {}).get('candles_stale') else 'candle_gaps' if (scanned or {}).get('candle_gaps') else 'market_outside_scope'
                            row.payload={**row.payload,'structure_check_status':reason,'structure_last_attempt_at':now_ms(),'structure_next_retry_at':0 if reason=='checked' else now_ms()+60000}
                            if (scanned or {}).get('scanned',0)>0:row.payload={**row.payload,'structure_checked_at':now_ms()}
                        emit(s,'observation_update',{'instruments':[iid]})
                await work(structure_checked)
                if context:
                    summary={**(scanned or {}),'plans_created':(planned or {}).get('snapshots',0)}
                    summary['waiting_inputs']=bool(summary.get('quote_stale') or summary.get('candles_stale') or summary.get('candle_gaps'))
                    def outcomes():
                        from engine.models import Episode,Campaign,Plan
                        with Session() as s:
                            rows=list(s.execute(select(Episode.phase,Episode.evidence['trigger']).join(Campaign).where(Campaign.reference_instrument_id==iid,Episode.phase.notin_(('ended','invalidated')))))
                            from engine.p38.plan_queries import live_plans
                            formed=bool(s.execute(live_plans(now_ms(),entry=True,instrument_ids=[iid]).limit(1)).first())
                        return 'plan_created' if formed else 'waiting_trigger' if any(not t for _,t in rows) else 'no_match'
                    summary['strategy_result']=await thread(outcomes)
                    if summary['strategy_result']=='plan_created' or (summary.get('p33_ready') and not summary.get('shadow_errors')):
                        summary['waiting_inputs']=False
                    await work(complete_result,context,summary,now_ms())
                # Handoff immediately, not after waiting for the next producer.
                from engine.p38.plan_queries import live_plans
                def new_entries():
                    with Session() as s:return [r.execution_instrument_id for r in s.execute(live_plans(now_ms(),entry=True,instrument_ids=[iid]))]
                entries=await thread(new_entries)
                if entries:
                    await thread(submit,entries,'plan_handoff',work_kind='entry')
                    await work(decision_list,instrument_ids=[iid],fast=True)
                # Entry publishes its current decision through the fast path.
                # Avoid retaining the mutation fence for another full build
                # before the first executable quote can be evaluated.
                else:await work(decision_list,instrument_ids=[iid])
            elif kind in ('entry','protect'):
                def refresh_and_publish():
                    updated=refresh_current([iid])
                    if isinstance(updated,dict) and updated.get('superseded'):return updated
                    return decision_list(instrument_ids=[iid],fast=True)
                await work(refresh_and_publish)
            elif kind=='projection':
                # Repair an unpublished immutable snapshot before candidate work.
                # The fast path rechecks TTL and native scope without replace_scope.
                await work(decision_list,instrument_ids=[iid],fast=True)
                from engine.p39.timing import scheduled_at,ORDINARY_MS
                cycle=scheduled_at(iid,now_ms(),ORDINARY_MS)
                if lease.get('projection_cycle')!=cycle:
                    await work(decision_list,instrument_ids=[iid])
                    lease['completed_projection_cycle']=cycle
        if keeper.done():await keeper
        await asyncio.to_thread(finish,lease)
    except asyncio.CancelledError:
        raise # lease expires after process exit; never acknowledge unfinished work
    except Exception as error:
        known={'analysis_lease_lost','analysis_inputs_superseded','analysis_source_changed','plan_inputs_superseded','worker_role_lost','owner_still_running'}
        code=str(error) if isinstance(error,RuntimeError) and str(error) in known else type(error).__name__
        if code not in known:
            from engine.db_retry import sqlstate
            logging.getLogger('erji.jobs').exception('analysis failed kind=%s instrument=%s stage=%s sqlstate=%s',kind,iid,stage,sqlstate(error))
        await asyncio.to_thread(finish,lease,code)
        if context:
            from engine.p39.pipeline import complete_result
            await asyncio.to_thread(complete_result,context,{},now_ms(),code)
        await asyncio.to_thread(heartbeat,'analysis_task_'+kind,'coalescing' if code in DEFERRED else 'error',{'instrument_id':iid,'reason':code,'stage':stage,'work_kind':kind,'owner':'p39'})
    finally:
        if context_token is not None:
            from engine.p39.pipeline import analysis_context
            analysis_context.reset(context_token)
        if keeper:keeper.cancel();await asyncio.gather(keeper,return_exceptions=True)
        try:
            if locked and connection:await thread(lambda:connection.execute(text('SELECT pg_advisory_unlock(hashtext(current_database() || :k))'),{'k':key}))
        finally:
            if connection:await thread(connection.close)

def managed_targets(at=None):
    from engine.p38.plan_queries import live_plans
    at=now_ms() if at is None else at
    with Session() as s:
        managed={r.execution_instrument_id for r in s.execute(live_plans(at))}
        entries={r.execution_instrument_id for r in s.execute(live_plans(at,entry=True))}
        active=s.get(UserPreference,'p37_active')
        observed=set((active.payload if active else {}).get('instrument_ids',[]))
        selections={}
        for kind in ('structure','trend'):
            row=s.get(UserPreference,'p39_'+kind+'_active')
            selections[kind]=sorted(set((row.payload if row else {}).get('instrument_ids',observed)))
        from engine.pd2.source_policy import REALTIME
        from engine.pd2.universe_policy import primary_eligible
        allowed={i.id for i in s.scalars(select(Instrument).where(Instrument.venue.in_(REALTIME))) if primary_eligible(i)}
    return {'protect':sorted(managed&allowed),'entry':sorted(entries&allowed),'projection':sorted((managed|observed)&allowed),'deep':sorted(observed&allowed),**{k:[i for i in v if i in allowed] for k,v in selections.items()}}

def retire_unneeded(targets):
    """Retain evidence and live leases; retire only obsolete queued work."""
    with Session.begin() as s:
        query=select(Task).where(Task.kind.in_(list(KINDS.values())),Task.state.in_(['queued','failed'])).with_for_update(skip_locked=True)
        for row in s.scalars(query):
            if row.payload.get('shadow'):continue
            allowed=targets['entry'] if row.kind==KINDS['entry'] else targets.get('protect',[]) if row.kind==KINDS['protect'] else targets.get(row.payload.get('work_kind','structure'),targets['deep'])
            if row.kind==KINDS['projection']:allowed=targets['projection']
            if row.payload.get('subject') not in allowed:
                row.state='complete';row.payload={**row.payload,'retired_reason':'no_live_entry' if row.kind==KINDS['entry'] else 'covered_by_entry_or_no_live_plan' if row.kind==KINDS['protect'] else 'outside_active_slots','finished_at':now_ms()}

async def run(once=False):
    import asyncio
    from engine.pd2.execution import ExecutionAdapter
    adapter=ExecutionAdapter();pending={};rotation=0;producer_progress={}
    with Session.begin() as s:
        for row in s.scalars(select(Task).where(Task.kind=='p38_analysis',Task.state!='leased',Task.state!='complete')):
            row.state='complete';row.payload={**row.payload,'retired_reason':'p39_typed_dispatcher','finished_at':now_ms()}
    async def produce():
        from engine.p38.lifecycle import thread
        while True:
            started=now_ms();producer_progress.update(started_at=started,in_progress=True)
            targets=await thread(managed_targets)
            # Entry already refreshes protection and publishes under one fence.
            # Keep a separate protect lane for plans without live entry windows.
            targets['protect']=sorted(set(targets['protect'])-set(targets['entry']))
            await thread(retire_unneeded,targets)
            for kind in ('protect','entry','projection'):await thread(submit,targets[kind],'managed_risk',work_kind=kind)
            producer_progress.update(finished_at=now_ms(),batch_ms=now_ms()-started,in_progress=False,targets={k:len(v) for k,v in targets.items()})
            if once:return
            await asyncio.sleep(max(.1,30-(now_ms()-started)/1000))
    async def report():
        reported=0
        while True:
            if now_ms()-reported>=60000:
                from engine.repository import heartbeat
                def counts():
                    at=now_ms()
                    with Session() as s:
                        rows=list(s.scalars(select(Task).where(Task.kind.in_(KINDS.values()))))
                        states={};by_kind={}
                        for row in rows:
                            states[row.state]=states.get(row.state,0)+1
                            item=by_kind.setdefault('baseline_shadow' if row.payload.get('shadow') else row.payload.get('work_kind','structure'),{'states':{},'completed':0,'failed':0,'deferred':0,'oldest_wait_ms':0,'queue_samples':[],'execution_samples':[]})
                            item['states'][row.state]=item['states'].get(row.state,0)+1
                            item['completed']+=row.payload.get('completed_count',0);item['failed']+=row.payload.get('failed_count',0)
                            item['deferred']+=row.payload.get('deferred_count',0)
                            if row.state in ('queued','failed'):item['oldest_wait_ms']=max(item['oldest_wait_ms'],max(0,at-row.payload.get('first_pending_at',row.available_at)))
                            if row.payload.get('finished_at',0)>=at-1200000:
                                item['queue_samples'].append(row.payload.get('queue_wait_ms',0));item['execution_samples'].append(row.payload.get('execution_ms',0))
                        for item in by_kind.values():
                            for key in ('queue','execution'):
                                samples=sorted(item.pop(key+'_samples'));item[key+'_samples']=len(samples)
                                item[key+'_p95_ms']=samples[min(len(samples)-1,int(len(samples)*.95))] if samples else None
                        return states,by_kind
                progress,by_kind=await asyncio.to_thread(counts)
                from engine.p39.pipeline import enabled,capacity_metrics
                demand_metrics=await asyncio.to_thread(capacity_metrics,now_ms()) if await asyncio.to_thread(enabled) else {'state':'not_activated'}
                from engine.db import metrics
                await asyncio.to_thread(heartbeat,'analysis_owner','partial' if progress.get('failed') else 'ok',{'tasks':progress,'by_work_kind':by_kind,'demand_metrics':demand_metrics,'producer':dict(producer_progress),'lanes':{lane:sum(key[0]==lane for key in pending)>0 for lane in LANES},'lane_slots':{lane:{'limit':LANE_LIMITS[lane],'running':sum(key[0]==lane for key in pending)} for lane in LANES},'timing_scope':'latest completion per subject/kind in last 20m; cumulative counts since P39','execution_owner':'p39_typed','database_timings':metrics()})
                reported=now_ms()
            if once:return
            await asyncio.sleep(60)
    producer=asyncio.create_task(produce());reporter=asyncio.create_task(report())
    try:
        if once:await producer
        while True:
            for supervisor in (producer,reporter):
                if supervisor.done():await supervisor
            for lane,(task,lease) in list(pending.items()):
                if task.done():await task;del pending[lane]
            lanes=list(LANES);lanes=lanes[rotation:]+lanes[:rotation];rotation=(rotation+1)%len(lanes)
            for lane in lanes:
                for slot in range(LANE_LIMITS[lane]):
                    key=(lane,slot)
                    if key in pending:continue
                    fast={'entry','protect','projection'}
                    excluded=[lease['instrument_id'] for task,lease in pending.values() if lane in fast and lease.get('work_kind') in fast]
                    lease=await asyncio.to_thread(take,work_kinds=LANES[lane],exclude_subjects=excluded)
                    if lease:pending[key]=(asyncio.create_task(execute(lease,adapter)),lease)
            if once:await asyncio.gather(*(t for t,_ in pending.values()));return
            if pending:
                await asyncio.wait([t for t,_ in pending.values()],timeout=5,return_when=asyncio.FIRST_COMPLETED)
            else:await asyncio.sleep(5)
    finally:
        producer.cancel();reporter.cancel()
        for task,_ in pending.values():task.cancel()
        await asyncio.gather(producer,reporter,*(t for t,_ in pending.values()),return_exceptions=True);await adapter.close()
