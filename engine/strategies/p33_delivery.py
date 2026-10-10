"""Frozen event publication; execution rules remain separate from research reports."""
from copy import deepcopy
from hashlib import sha256
import json
from engine.models import RawEvent, Campaign, Episode, Plan, SignalSnapshot
from engine.pd2.analysis import technical
from engine.pd2.config import AnalysisConfig, ValidationConfig
from engine.pd2.chart_contract import drawing_contract
from engine.pd2.snapshots import freeze_datasets
from engine.pd2.event_overlays import attach_event_overlays
from engine.strategies.p33_cases import match_cases


VERSION = 'p35-delivery-v1'


def make_snapshot(event, bars, market, tick, now, config=None, overlays=None, derivative_context=None, derivative_history=None):
    hit = event['initial']; plan = event['plan']; decision = event.get('qualification') or {}
    assessments = [technical(bars.get(tf, []), tf, now, tick, config or AnalysisConfig(),pivot_tie='last' if plan.get('structural_version') else 'first') for tf in ('60','240','1D','1W')]
    if plan.get('structural_version'):
        for assessment in assessments:
            tf=assessment['timeframe']
            assessment['coverage']=deepcopy(plan.get('target_coverage',{}).get(tf,assessment.get('coverage')))
            assessment['structural_version']=plan['structural_version']
            # Frozen selected levels can predate the visible/indicator window.
            for target in plan['targets'].values():
                if target and target.get('timeframe')==tf and target.get('id') and target.get('structural_version')==plan['structural_version']:
                    assessment['levels']=[v for v in assessment['levels'] if v['id']!=target['id']]+[deepcopy(target)]
    evidence = {**deepcopy(hit), 'instrument_id': hit['market_id'], 'template': hit['strategy_id'],
        'origin':deepcopy(event.get('origin') or {'origin_type':'legacy_unlinked'})}
    end = event.get('invalidated_at') or event.get('protection_observed_at')
    phase = 'invalidated' if end else 'early_trigger' if hit['strategy_id']=='early_strength' else 'structure_confirmed'
    targets = {k: deepcopy(plan['targets'].get(k)) for k in ('t1','daily','weekly')}
    action = deepcopy(decision.get('rule_action') or {'code':'paused','label':'等待数据','reason':'当前参与判断所需数据不足'})
    snap = {'delivery_version': VERSION, 'instrument_id': hit['market_id'], 'asset_id': (market or {}).get('asset_id'),
            'episode_id': hit['event_id'], 'event_id': hit['event_id'], 'strategy_id': hit['strategy_id'],
            'strategy_label': hit['label'], 'strategy_version': hit['strategy_version'], 'rule_version': hit['strategy_version'],
            'as_of': now, 'known_at': now, 'first_seen_at': event['first_observed_at'],
            'first_seen_price': hit['trigger']['close'], 'phase': phase,
            'phase_label': '结构失效' if end else hit['label'], 'market': deepcopy(market or {
                'instrument_id': hit['market_id'], 'price': None, 'quote_asset': None}),
            'evidence': evidence, 'price_pattern': deepcopy(hit), 'action': action,
            'origin':deepcopy(evidence['origin']),'pipeline_tier':'T3','analysis_market_id':hit['market_id'],
            'valid_until': now, 'execution': decision.get('execution'), 'research_qualification': decision,
            'original_protection': hit['protection'], 'current_protection': event.get('current_protection', hit['protection']),
            'opportunity_plan': {**targets, 'highest_conditional_target': None, 'intermediate_levels': []},
            'tracking': {'target_first_observed_at': {k:v['observed_at'] for k,v in event.get('target_first_observed',{}).items()},
                         'protection_history': event.get('protection_history',[]),
                         'first_invalidation_observed_at': event.get('invalidated_at')},
            'trade_thesis': {'headline': hit['label']+' · '+action['label'], 'support': [hit['reason']],
                            'opposition': [], 'missing': list(plan.get('missing_targets',{}).values())},
            'timeframe_assessments': assessments, 'candles_at_snapshot': deepcopy(bars),
            'derivatives': deepcopy(derivative_context),
            'derivative_history_at_snapshot': deepcopy(derivative_history) if derivative_history else {'instrument_id': hit['market_id'], 'oi': [], 'funding': [], 'oi_status': 'not_frozen', 'funding_status': 'not_frozen'},
            'trading_enabled': False, 'case_matches': match_cases(hit, now),
            'series_revision': sha256(json.dumps(bars,sort_keys=True).encode()).hexdigest(),
            'validation_status': 'unvalidated', 'probability': None}
    snap['event_overlays'] = attach_event_overlays(assessments, bars, evidence, now, overlays)
    if overlays and overlays.get('avwap'):
        snap['event_overlays']['avwap'] = overlays['avwap']
        points = {p['time']:p['event_avwap'] for p in overlays.get('avwap_series',[])}
        for a in assessments:
            if a['timeframe'] == hit['timeframe']:
                for p in a['series']: p['event_avwap'] = points.get(p['time'])
    snap['id'] = 'p33-research-' + sha256(json.dumps(snap, sort_keys=True).encode()).hexdigest()[:40]
    snap['drawing_contract'] = drawing_contract(snap)
    return snap


def store(session, snapshot, now):
    if not session.get(RawEvent, snapshot['id']):
        session.add(RawEvent(id=snapshot['id'],source='erji',endpoint='p33_snapshot',received_at=now,
                            payload=freeze_datasets(session,snapshot)))


def publish(session, snapshot, event, instrument, config, now, group_id):
    """Publish real events; research reports do not grant execution eligibility."""
    if snapshot["strategy_id"] in config.disabled_strategies:
        return {"enabled":False,"reasons":["用户关闭此策略"]}
    release = {"enabled":True,"reasons":[],"policy":"p35_no_report_gate","validation_status":"unvalidated"}
    from engine.strategies.p33 import VERSION,CONFIG_HASH
    from engine.strategies.p33_lifecycle import CONFIG_HASH as PLAN_HASH
    hit=event['initial']
    if (hit.get('strategy_version'),hit.get('config_hash'),event['plan'].get('config_hash'))!=(VERSION,CONFIG_HASH,PLAN_HASH) or instrument is None or instrument.id!=snapshot['instrument_id']:
        return {'enabled':False,'reasons':['事件身份或冻结规则版本不一致']}
    qualification = event.get('qualification') or {}
    action = qualification.get('rule_action') or {}
    # A release does not retroactively revive old or expired research triggers.
    if action.get('code') not in ('participate','trial','waiting','no_chase','paused','expired','ended'):
        return {'enabled':False,'reasons':['执行资格尚未形成']}
    episode_id = snapshot['event_id']; plan_id = episode_id+':p33'
    existing = session.get(Plan, plan_id)
    if not existing and now >= event['plan']['expires_at']:
        return {'enabled':False,'reasons':['首次发布时参与窗口已结束，保留历史研究记录']}
    campaign_id = group_id
    if not session.get(Campaign,campaign_id):
        session.add(Campaign(id=campaign_id,asset_id=instrument.asset_id,reference_instrument_id=instrument.id,
                            first_seen_at=event['first_observed_at'],first_seen_price=event['initial']['trigger']['close'],state='active'))
        session.flush()
    episode = session.get(Episode,episode_id)
    if not episode:
        session.add(Episode(id=episode_id,campaign_id=campaign_id,strategy_id=snapshot['strategy_id'],
                            phase=snapshot['phase'],rule_version=snapshot['rule_version'],evidence=snapshot['evidence']))
        session.flush()
    else: episode.phase = snapshot['phase']
    if not existing:
        session.add(Plan(id=plan_id,episode_id=episode_id,execution_instrument_id=instrument.id,revision=1,
                         expires_at=event['plan']['expires_at'],payload={'p33': True,'frozen':event['plan'],'release':release,
                         'config':config.model_dump(),'evidence':snapshot['evidence'],'original_t1':event['plan']['targets'].get('t1'),
                         'original_protection':event['plan']['original_protection'],'template':snapshot['strategy_id'],
                         'opportunity':snapshot['opportunity_plan'],'created_at':now,
                         'origin':deepcopy(snapshot.get('origin') or {'origin_type':'legacy_unlinked'})}))
        session.flush()
    snap = deepcopy(snapshot)
    from engine.pd2.portfolio import portfolio,plan_risk
    sizing=plan_risk(portfolio()['risk'],event['plan']['entry_zone'][1],snapshot['current_protection'],snapshot['execution'])
    snap.update(id=plan_id+':'+str(now),plan_id=plan_id,action=action,trading_enabled=False,validation_status='unvalidated',release=release,
                position_sizing=sizing,valid_until=min(now+config.execution.quote_max_age_seconds*1000,event['plan']['expires_at'],
                    (snapshot.get('execution') or {}).get('received_at',0)+config.execution.depth_max_age_seconds*1000) if action['code'] in ('participate','trial') else now+90000)
    snap['evidence']['protection']=snap['current_protection']
    snap['trade_thesis']['headline']=snap['price_pattern']['label']+' · '+action['label']
    snap['drawing_contract'] = drawing_contract(snap)
    if not session.get(SignalSnapshot,snap['id']):
        session.add(SignalSnapshot(id=snap['id'],plan_id=plan_id,as_of=now,payload=freeze_datasets(session,snap)))
        from engine.repository import emit
        emit(session,'signal_update',{'asset_id':instrument.asset_id,'episode_id':episode_id,'snapshot_id':snap['id'],'strategy_id':snapshot['strategy_id']})
    return release
