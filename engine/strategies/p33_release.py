"""Legacy research-report assessment retained for historical reproducibility.
P35 runtime publication does not call this module; these report checks cannot block signals.
"""
from engine.models import UserPreference, SnapshotDataset
from engine.strategies.p33 import VERSION, CONFIG_HASH
from engine.strategies.p33_lifecycle import CONFIG_HASH as PLAN_HASH
from engine.strategies.p33_replay import CONFIG_HASH as COST_HASH
from hashlib import sha256
import json

CHECKS = ('causal_replay', 'chart_contract', 'out_of_sample', 'failed_and_delisted',
          'forward_observation', 'costs', 'baseline_comparison')


def sample_counts(records, strategy, now):
    """Count independent mature events, not 24h/72h rows or market aliases."""
    events={};historical=set();forward=set();rejected=0
    for r in records:
        if (r.get('strategy_id')!=strategy or r.get('status')!='completed_cost_scenario'
            or not r.get('canonical_asset_id') or not r.get('identity_artifact')
            or not r.get('source_artifact') or not r.get('event_id')
            or not isinstance(r.get('entry_bar_open'),int) or not isinstance(r.get('exit_bar_close'),int)
            or not 0<r['entry_bar_open']<r['exit_bar_close']<=now):
            rejected+=1;continue
        key=r['event_id'];identity=(r['canonical_asset_id'],r['entry_bar_open'])
        previous=events.get(key)
        if previous and previous!=identity:
            raise ValueError('conflicting_event_identity_or_entry')
        events[key]=identity
        day=r['entry_bar_open']//86400000
        if r.get('mode')=='historical_replay':historical.add(day)
        elif (r.get('mode')=='forward_observation' and isinstance(r.get('recorded_at'),int)
              and r['recorded_at']<=r['entry_bar_open']):forward.add(day)
        else:raise ValueError('unknown_sample_mode_or_late_forward_record')
    return {'mature_fills':len(events),'assets':len({v[0] for v in events.values()}),
        'historical_days':len(historical),'forward_days':len(forward),
        'rejected_rows':rejected,'unit':'unique_event_cost_scenario_not_executed_trade'}


def verified_sample(session,report,strategy,now):
    manifest=session.get(SnapshotDataset,report.get('sample_artifact')) if report.get('sample_artifact') else None
    if not manifest:raise ValueError('missing_sample_artifact')
    payload=manifest.payload
    if sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()!=manifest.id:
        raise ValueError('sample_artifact_hash_mismatch')
    rows=payload.get('records',[])
    for row in rows:
        for key in ('identity_artifact','source_artifact'):
            ref=row.get(key);artifact=session.get(SnapshotDataset,ref) if ref else None
            if not artifact or sha256(json.dumps(artifact.payload,sort_keys=True).encode()).hexdigest()!=ref:
                raise ValueError('sample_evidence_missing_or_changed')
        identity=session.get(SnapshotDataset,row['identity_artifact']).payload
        if identity.get('status')!='verified' or identity.get('canonical_asset_id')!=row.get('canonical_asset_id'):
            raise ValueError('sample_asset_identity_unverified')
        source=session.get(SnapshotDataset,row['source_artifact']).payload
        if source.get('event_id')!=row.get('event_id') or source.get('strategy_id')!=strategy:
            raise ValueError('sample_event_source_mismatch')
        if any(source.get(k)!=row.get(k) for k in ('status','entry_bar_open','exit_bar_close','horizon_hours')):
            raise ValueError('sample_cost_scenario_mismatch')
    counts=sample_counts(rows,strategy,now)
    # The protocol must explicitly state which days the unchanged threshold
    # uses. Historical and forward observations never silently substitute.
    protocol=session.get(SnapshotDataset,report.get('protocol_artifact')) if report.get('protocol_artifact') else None
    if not protocol or protocol.created_at>report.get('protocol_registered_at',0):raise ValueError('protocol_not_frozen_before_evaluation')
    if sha256(json.dumps(protocol.payload,sort_keys=True).encode()).hexdigest()!=protocol.id:raise ValueError('protocol_hash_mismatch')
    basis=protocol.payload.get('sample_days_basis')
    if basis not in ('historical_days','forward_days'):raise ValueError('sample_days_basis_unspecified')
    counts['days']=counts[basis];counts['days_basis']=basis
    if any(report.get('sample',{}).get(k)!=counts[k] for k in ('mature_fills','assets','days')):
        raise ValueError('declared_sample_counts_mismatch')
    return counts


def assess(report, config, now, strategy):
    reasons = []
    if not report: return {'enabled': False, 'reasons': ['尚无通过审核的验证报告']}
    if (report.get('strategy_id'), report.get('rule_version'), report.get('config_hash'),
        report.get('plan_hash'), report.get('cost_hash')) != (strategy, VERSION, CONFIG_HASH, PLAN_HASH, COST_HASH):
        reasons.append('规则或参数版本不一致')
    if not report.get('approved_at') or not report['approved_at'] <= now < report.get('expires_at', 0):
        reasons.append('审核未完成或已过期')
    if not report.get('reviewer') or not report.get('protocol_registered_at') or report['protocol_registered_at'] >= report.get('evaluation_started_at', 0):
        reasons.append('缺事前冻结的评估方案或审核人')
    counts = report.get('sample', {})
    for key, minimum in [('mature_fills', config.min_mature_fills), ('assets', config.min_assets), ('days', config.min_days)]:
        if counts.get(key, 0) < minimum: reasons.append(key + '样本不足')
    for key in CHECKS:
        check = report.get('checks', {}).get(key, {})
        if check.get('passed') is not True or not check.get('artifact_sha256'):
            reasons.append(key + '尚未通过并留存证据')
    return {'enabled': not reasons, 'reasons': reasons, 'report_id': report.get('id')}


def gate(session, strategy, config, now, event=None):
    pointer = session.get(UserPreference, 'p33_release:' + strategy)
    identity = (pointer.payload if pointer else {}).get('report_dataset')
    dataset = session.get(SnapshotDataset, identity) if identity else None
    report = dataset.payload if dataset else None
    decision = assess(report, config, now, strategy)
    if decision['enabled']:
        frozen={'analysis':(event or {}).get('analysis_config'),'execution':(event or {}).get('execution_config')}
        expected=sha256(json.dumps(frozen,sort_keys=True).encode()).hexdigest()
        if event is None or report.get('runtime_config_hash')!=expected:
            decision['reasons'].append('事件执行/保护配置未被该报告验证')
            decision['enabled']=False
    if decision['enabled']:
        try:decision['verified_sample']=verified_sample(session,report,strategy,now)
        except (ValueError,TypeError,KeyError) as error:
            decision['reasons'].append('样本统计证据未通过:'+str(error));decision['enabled']=False
    if decision['enabled']:
        # Every referenced artifact must actually be retained, not just named.
        for check in report['checks'].values():
            artifact=session.get(SnapshotDataset, check['artifact_sha256'])
            if not artifact or sha256(json.dumps(artifact.payload,sort_keys=True).encode()).hexdigest()!=artifact.id:
                decision['reasons'].append('验证附件缺失或哈希不符')
        decision['enabled'] = not decision['reasons']
    return decision
