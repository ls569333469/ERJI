"""Persistent pre-trigger structures; no fabricated trigger or trade plan."""
from copy import deepcopy
from decimal import Decimal as D
from hashlib import sha256
import json
from sqlalchemy import select
from engine.models import Episode,Campaign,RawEvent
from engine.pd2.analysis import technical,DURATIONS
from engine.strategies.p33_lifecycle import freeze_plan
from engine.strategies.p33 import VERSION


def sync_candidates(session,instrument,result,bars,market,now,config,structural_context=None):
    if not getattr(instrument,'asset_id',None):return
    existing=list(session.scalars(select(Episode).join(Campaign).where(
        Campaign.reference_instrument_id==instrument.id,Episode.phase=='structure_pending',
        Episode.rule_version==VERSION)))
    by_rule={r['strategy_id']:r for r in result.get('results',[])}
    kept=set()
    for rule,hit in by_rule.items():
        if rule in config.disabled_strategies or hit.get('status')!='waiting' or not hit.get('anchors'):continue
        if not market or market.get('price') is None or D(hit['protection'])<=0:continue
        if D(str(market['price']))<=D(hit['protection']):continue
        identity=[instrument.id,rule,hit['strategy_version'],[(a.get('time') or a.get('start_time'),a['price']) for a in hit['anchors']]]
        event_id='p35-candidate-'+sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:24]
        kept.add(event_id);old=session.get(Episode,event_id)
        # A terminal candidate is never silently revived.
        if old and old.phase!='structure_pending':continue
        pattern={**deepcopy(hit),'event_id':event_id}
        plan=freeze_plan(pattern,bars,now,instrument.spec['tick_size'],config.analysis,structural_context)
        tf=hit['timeframe'];label={'60':'1H','240':'4H'}.get(tf,tf)
        evidence={**pattern,'template':rule,'instrument_id':instrument.id,'price_pattern':pattern,
            'evaluated_at':now,'structure_as_of':max(a['known_at'] for a in hit['anchors']),
            'structure_valid_until':now+360000,'pending_volume_multiple':hit['volume_multiple'],
            'trigger_requirements':{'timeframe':tf,'close_above':hit['threshold'],'relative_volume_min':'1.5'},
            'protection_reference':{'price':hit['protection'],'status':'estimated','timeframe':tf,'method':'pattern_protection'},
            'invalidation':{'price':hit['protection'],'timeframe':tf,'condition':'close_below'},
            'reason':f"{hit['label']}：等待 {label} 收盘高于 {hit['threshold']}，量比至少 1.50（当前 {D(hit['volume_multiple']):.2f}）。",
            'targets_t1':plan['targets'].get('t1'),'target_coverage':plan['target_coverage'],'structural_version':plan['structural_version'],'targets_4h':plan['targets'].get('four_hour'),'targets_1d':plan['targets'].get('daily'),'targets_1w':plan['targets'].get('weekly'),
            'timeframe_assessments':[technical(bars.get(t,[]),t,now,instrument.spec['tick_size'],config.analysis) for t in ('60','240','1D','1W')]}
        from engine.p39.pipeline import origin
        evidence['origin']=deepcopy(old.evidence.get('origin') or origin()) if old else origin()
        evidence['latest_analysis']=origin()
        from engine.strategies.p33_lifecycle import DEPENDENCIES
        evidence['required_inputs']=dict(DEPENDENCIES.get(rule,{}))
        from engine.pd2.structural_history import merge
        evidence['timeframe_assessments']=[merge(a,(structural_context or {}).get(a['timeframe']),hit['entry_zone'][1],60) for a in evidence['timeframe_assessments']]
        for assessment in evidence['timeframe_assessments']:
            for target in plan['targets'].values():
                if target and target['timeframe']==assessment['timeframe'] and not any(v['id']==target['id'] for v in assessment['levels']):
                    assessment['levels'].append(deepcopy(target))
        # Only the summary is needed for list candidates, not all indicator points.
        for assessment in evidence['timeframe_assessments']:assessment.pop('series',None)
        if old:old.evidence=evidence
        else:
            session.add(Campaign(id=event_id,asset_id=instrument.asset_id,reference_instrument_id=instrument.id,
                first_seen_at=now,first_seen_price=market['price'],state='active'));session.flush()
            session.add(Episode(id=event_id,campaign_id=event_id,strategy_id=rule,phase='structure_pending',rule_version=VERSION,evidence=evidence))
            session.add(RawEvent(id=event_id,source='erji',endpoint='p35_candidate_first',received_at=now,payload=deepcopy(evidence)))
            from engine.repository import emit
            emit(session,'signal_update',{'asset_id':instrument.asset_id,'episode_id':event_id,'phase':'structure_pending','strategy_id':rule})
    for old in existing:
        if old.id in kept:continue
        hit=by_rule.get(old.strategy_id,{})
        if hit.get('status')=='missing_data' and old.strategy_id not in config.disabled_strategies:
            old.evidence={**old.evidence,'reason':hit['reason'],'data_status':'missing_data'}
            continue
        old.phase='ended';old.evidence={**old.evidence,'ended_at':now,'reason':'用户关闭策略' if old.strategy_id in config.disabled_strategies else '原候选已触发、失效或形成新结构'}
        key=old.id+':end'
        if not session.get(RawEvent,key):session.add(RawEvent(id=key,source='erji',endpoint='p35_candidate_ended',received_at=now,payload={'event_id':old.id,'reason':old.evidence['reason']}))


def candidate_drawing(event_id,market_id,evidence,phase,as_of):
    p=evidence.get('price_pattern') or {}
    if phase!='structure_pending' or p.get('status')!='waiting' or p.get('market_id')!=market_id:return None
    from engine.pd2.chart_contract import _price
    if p.get('strategy_version')!=VERSION or evidence.get('data_status')=='missing_data' or evidence.get('structure_valid_until',0)<as_of:return None
    anchors=p.get('anchors',[])
    if not _price(p.get('threshold')) or any(not _price(a.get('price')) for a in anchors):return None
    if not anchors or any(not 0<(a.get('time') or a.get('start_time') or 0)<=a.get('known_at',0)<=as_of for a in anchors):return None
    anchors=deepcopy(anchors)+[{'kind':'trigger_boundary','price':p['threshold'],'known_at':max(a['known_at'] for a in anchors)}]
    return {'snapshot_id':None,'market_id':market_id,'event_id':event_id,
        'series_revision':sha256(json.dumps(evidence,sort_keys=True).encode()).hexdigest(),
        'strategy_id':p['strategy_id'],'strategy_version':p['strategy_version'],'timeframe':p.get('source_timeframe',p['timeframe']),
        'as_of':as_of,'closed_at':None,'coverage_start':min(a.get('time') or a.get('start_time') or a['known_at'] for a in anchors),
        'coverage_end':as_of,'status':'candidate','anchors':anchors,'required_indicators':p.get('required_indicators',[]),
        'entry_zone':None,'protection':None,'first_target':None,'protection_history':[]}
