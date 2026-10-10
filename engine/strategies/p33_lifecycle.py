"""Frozen research plans and structural identity. No trading publication or I/O."""
from copy import deepcopy
from decimal import Decimal as D
from hashlib import sha256
import json

from engine.pd2.analysis import DURATIONS, closed_bars
from engine.pd2.event_overlays import pivots
from engine.pd2.decision import execution_cost, decide
from engine.strategies.p33 import Facts, Missing, PRIORITY

VERSION = 'p33-lifecycle-v2'
POLICY = {
    'merge': 'same_market_source_and_trigger_timeframe_close_exact_boundary_parent_and_rule_config',
    'targets': 'nearest_confirmed_pivot_above_entry_high_across_4h_1d_1w',
    'target_zone_atr': '.25', 'target_atr': 'latest_known_closed_bar_in_target_timeframe',
    'tie_break': ['240', '1D', '1W'], 'release': 'shadow_only',
    'followup_dependencies': 'all_required_timeframes_contiguous_and_fresh',
}
CONFIG_HASH = sha256(json.dumps(POLICY, sort_keys=True).encode()).hexdigest()[:16]
DEPENDENCIES = {
    'recent_high': {'240': 52, '1D': 30}, 'ath': {'240': 52},
    'squeeze': {'240': 145}, 'double_bottom': {'240': 52},
    'early_strength': {'60': 52, '240': 52},
    'trend_pullback': {'60': 52, '240': 52, '1D': 52},
    'failed_breakdown': {'60': 52, '240': 52}, 'retest': {'60': 52},
    'repeated_test': {'240': 271, '1D': 45},
}


def identity(hit):
    # Deliberately exact: nearby but distinct boundaries are not the same structure.
    values = [VERSION, CONFIG_HASH, hit['market_id'], hit['strategy_version'], hit['config_hash'],
              hit.get('source_timeframe', hit['timeframe']), hit['timeframe'], hit['trigger']['close_time'],
              format(D(hit['boundary']).normalize(), 'f'), hit.get('parent_event_id')]
    return 'p33-group-' + sha256(json.dumps(values).encode()).hexdigest()[:24]


def group_hits(hits):
    groups = {}
    for hit in hits:
        if hit.get('status') != 'triggered':
            continue
        groups.setdefault(identity(hit), []).append(hit)
    return {key: sorted(values, key=lambda v: (PRIORITY.index(v['strategy_id']), v['event_id']))
            for key, values in groups.items()}


def freeze_plan(hit, bars, now, tick, config=None, structural_context=None):
    """Freeze shared native resistance evidence; unavailable targets remain references."""
    from engine.pd2.analysis import structural
    targets,missing,coverage={},{},{}
    entry=D(hit['entry_zone'][1])
    for tf,name in [('240','four_hour'),('1D','daily'),('1W','weekly')]:
        assessment=(structural_context or {}).get(tf)
        if assessment is None:assessment=structural(bars.get(tf,[]),tf,now,tick,config)
        coverage[tf]=deepcopy(assessment['coverage'])
        levels=[v for v in assessment['levels'] if v['kind']=='resistance' and D(v['low'])>entry] if assessment['status']=='fresh' else []
        value=min(levels,key=lambda v:(D(v['low']),v['id']),default=None)
        targets[name]=deepcopy(value)
        if value is None:missing[name]='无上方已确认结构阻力' if assessment['status']=='fresh' else '闭合历史不足、断档或已过期'
    targets['t1']=deepcopy(min((v for v in targets.values() if v),key=lambda v:(D(v['low']),v['timeframe']),default=None))
    return {'version':VERSION,'config_hash':CONFIG_HASH,'as_of':now,
        'market_id':hit['market_id'],'event_id':hit['event_id'],
        'entry_zone':deepcopy(hit['entry_zone']),'original_protection':hit['protection'],
        'invalidation':deepcopy(hit['invalidation']),'expires_at':hit['expires_at'],
        'targets':targets,'missing_targets':missing,'trading_enabled':False,
        'structural_version':'pd2-shared-structure-v2','target_coverage':coverage}


def qualify(event, market, book, config, now, bars=None, parent_active=None):
    """Use the existing executable-book gate, but never grant release here."""
    hit = event['initial']
    plan = event.get('plan') or {}
    evidence = {**hit, 'protection': event.get('current_protection', hit['protection'])}
    target = (plan.get('targets') or {}).get('t1')
    execution = {'status': 'unavailable', 'net_r': None, 'reasons': ['可成交性配置缺失']}
    quote = None
    issues = []
    if market and market.get('instrument_id') != hit['market_id']:
        issues.append('market_mismatch')
        market = None
    if book and book.get('instrument_id') != hit['market_id']:
        issues.append('book_market_mismatch')
        book = None
    if book and book.get('asks'):
        from engine.pd2.book_clock import effective_time
        quote = {'price': book['asks'][0][0], 'price_kind': 'ask', 'as_of': effective_time(book) or 0}
    elif market:
        quote = {'price': market.get('price'), 'price_kind': market.get('price_kind'), 'as_of': market.get('as_of', 0)}
    early = hit['strategy_id'] == 'early_strength'
    if config:
        venue = hit['market_id'].split(':')[0]
        amount = str(config.probe_notional_usd)
        # A USD probe cannot be silently reinterpreted as USDT or another quote.
        fx = (market or {}).get('quote_usd_rate')
        if (market or {}).get('quote_asset') == 'USD':
            fx = '1'
        if fx is None or not D(str(fx)).is_finite() or D(str(fx)) <= 0:
            execution = {'status': 'unavailable', 'net_r': None, 'reasons': ['报价币USD换算缺失']}
        else:
            execution = execution_cost(quote['price'] if quote else hit['entry_zone'][1], evidence['protection'],
                target.get('low') if target else None, book, now, notional=str(D(amount)/D(str(fx))),
                fee_rate=config.taker_fee_rates.get(venue), funding_cost_rate=config.funding_scenario_rate,
                min_r=config.early_min_net_r if early else config.confirmed_min_net_r,
                max_spread_bps=config.max_spread_bps, max_age_ms=config.depth_max_age_seconds*1000)
            execution.update(probe_notional_usd=amount,quote_asset=market.get('quote_asset'),quote_usd_rate=str(fx))
    phase = 'invalidated' if event.get('invalidated_at') or event.get('protection_observed_at') else 'early_trigger' if early else 'structure_confirmed'
    action = decide(evidence, phase, quote, now, execution,
                    max_quote_age_ms=(config.quote_max_age_seconds*1000 if config else 90000))
    def pause(reason, code):
        nonlocal action
        # Expiry is the primary state; retain every unmet dependency as evidence.
        action = {**action, 'reason_codes': list(dict.fromkeys(action['reason_codes']+[code]))}
        if action['code'] != 'expired':
            action = {**action, 'code': 'paused', 'label': '暂停参与', 'reason': reason}
    latest = event.get('checked_through', hit['trigger']['close_time'])
    if phase != 'invalidated' and not 0 <= now-latest <= DURATIONS[hit['timeframe']]+180000:
        pause('后续闭合K线已过期', 'candles_stale')
    dependencies = {}
    if bars is not None:
        facts = Facts(bars, now, '1')  # Only continuity/freshness; price precision is irrelevant here.
        for tf, count in DEPENDENCIES[hit['strategy_id']].items():
            try:
                rows = facts.get(tf, count)
                dependencies[tf] = {'status': 'fresh', 'closed_at': rows[-1]['close_time']}
            except Missing as error:
                dependencies[tf] = {'status': 'unavailable', 'reason': str(error)}
        if any(v['status'] != 'fresh' for v in dependencies.values()) and phase != 'invalidated':
            pause('依赖周期的闭合K线不足、断档或过期', 'dependency_candles_unavailable')
    if hit['strategy_id'] == 'retest' and parent_active is False and phase != 'invalidated':
        pause('原突破事件已失效', 'parent_structure_ended')
    if event.get('coverage_gap_pending_at') is not None and phase != 'invalidated':
        pause('事件追踪存在尚未补齐的K线缺口', 'tracking_gap')
    return {'as_of': now, 'rule_action': action, 'execution': execution, 'issues': issues,
            'dependency_data': dependencies,
            'release_status': 'technical_analysis', 'trading_enabled': False}


def follow(event, bars, now):
    result = deepcopy(event)
    if result.get('invalidated_at') is not None or result.get('protection_observed_at') is not None:
        return result
    hit = result['initial']
    if now >= hit['expires_at'] and result.get('entry_expired_at') is None:
        result['entry_expired_at'] = now
    tf = hit['invalidation']['timeframe']
    start = hit['trigger']['close_time']
    rows = [b for b in closed_bars(bars.get(tf, []), now) if b['open_time'] >= start]
    targets = (result.get('plan') or {}).get('targets') or {}
    first_plan_at = (result.get('plan') or {}).get('as_of', now)
    stop = D(result.get('current_protection', hit['protection']))
    prior_close = result.get('checked_through', start)
    for bar in rows:
        if bar['close_time'] <= prior_close:
            continue
        if bar['open_time'] != prior_close:
            result.setdefault('coverage_gap_first_observed_at', now)
            result['coverage_gap_pending_at'] = prior_close
            break  # Do not skip an unseen stop/target or advance the replay cursor.
        result.pop('coverage_gap_pending_at', None)
        # A bar that touches both the stop and a target has unknown intrabar order.
        # A protection adjustment observed later cannot apply to an earlier low.
        known_steps=[p for p in result.get('protection_history',[]) if p['observed_at']<=bar['open_time']]
        bar_stop=D(known_steps[-1]['price']) if known_steps else D(hit['protection']) if result.get('protection_history') else stop
        stop_hit = D(bar['low']) <= bar_stop
        touched = {name: target for name, target in targets.items() if target and name != 'four_hour'
                   and bar['open_time'] >= max(first_plan_at, target['known_at']) and D(bar['high']) >= D(target['low'])}
        reached = result.setdefault('target_first_observed', {})
        for name, target in touched.items():
            if name not in reached:
                reached[name] = {'observed_at': now, 'bar_open_time': bar['open_time'],
                    'bar_close_time': bar['close_time'], 'price': target['low'],
                    'intrabar_order': 'unknown_stop_and_target' if stop_hit else 'target_only',
                    'execution_confirmed': False}
        if stop_hit:
            result.update(protection_observed_at=now, protection_bar_open=bar['open_time'], status='protection_touched')
        if D(bar['close']) < D(hit['invalidation']['price']):
            result.update(invalidated_at=now, invalidation_close_time=bar['close_time'], status='invalidated')
        result['checked_through'] = bar['close_time']
        prior_close = bar['close_time']
        if stop_hit or result.get('invalidated_at') is not None:
            break
    return result
