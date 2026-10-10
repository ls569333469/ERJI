"""Deterministic OHLC cost scenario, not a claim of historical fills or profits."""
from decimal import Decimal as D
from hashlib import sha256
import json
from engine.pd2.analysis import DURATIONS

VERSION = 'p33-cost-replay-v2'
POLICY = {'entry': 'next_bar_open_inside_frozen_zone_before_expiry',
          'delayed_observation': 'expired_before_next_open_is_not_missing_history',
          'exit': 'first_target_or_original_stop_or_fixed_horizon',
          'same_bar': 'stop_first', 'gap_stop': 'adverse_open',
          'taker_fee_each_side': '.0005', 'slippage_each_side': '.0005',
          'funding_allowance_per_trade': '.001', 'horizons_hours': [24, 72],
          'min_net_r_early': '1.5', 'min_net_r_confirmed': '1.8',
          'trading_enabled': False}
CONFIG_HASH = sha256(json.dumps(POLICY, sort_keys=True).encode()).hexdigest()[:16]


def simulate(event, rows, horizon_hours):
    if horizon_hours not in POLICY['horizons_hours']:
        raise ValueError('unregistered_horizon')
    hit, plan = event['initial'], event['plan']
    target = plan['targets'].get('t1')
    result = {'event_id': hit['event_id'], 'strategy_id': hit['strategy_id'],
              'horizon_hours': horizon_hours, 'version': VERSION, 'config_hash': CONFIG_HASH,
              'status': 'missing_target', 'trading_enabled': False,
              'cost_kind': 'configured_scenario_not_historical_execution', 'net_return_pct': None}
    if not target:
        return result
    start = max(hit['known_at'], plan['as_of'], target['known_at'])
    duration = DURATIONS[hit['timeframe']]
    rows = sorted([row for row in rows if row['open_time'] >= start and row.get('is_closed')], key=lambda r:r['open_time'])
    # Do not credit a later bar when the first possible order interval is missing.
    expected_open = ((start+duration-1)//duration)*duration
    if expected_open >= plan['expires_at']:
        return {**result,'status':'entry_window_elapsed_before_next_open'}
    if not rows or rows[0]['open_time'] != expected_open:
        return {**result, 'status': 'missing_entry_history'}
    fee, slip, funding = (D(POLICY[k]) for k in ('taker_fee_each_side','slippage_each_side','funding_allowance_per_trade'))
    low, high = map(D, plan['entry_zone'])
    stop, goal = D(plan['original_protection']), D(target['low'])
    entry_bar = None
    rejected_cost = False
    previous_open = expected_open-duration
    for bar in rows:
        if bar['open_time'] >= plan['expires_at']:
            break
        if bar['open_time'] != previous_open+duration:
            return {**result, 'status': 'missing_entry_history'}
        previous_open = bar['open_time']
        entry = D(bar['open'])*(1+slip)
        if low <= entry <= high and 0 < stop < entry < goal:
            profit=goal*(1-slip)*(1-fee)-entry*(1+fee)-entry*funding
            risk=entry*(1+fee)+entry*funding-stop*(1-slip)*(1-fee)
            net_r=profit/risk
            minimum=D(POLICY['min_net_r_early' if hit['strategy_id']=='early_strength' else 'min_net_r_confirmed'])
            if net_r<minimum:
                rejected_cost=True
                continue
            entry_bar = bar
            break
    if entry_bar is None:
        return {**result, 'status': 'insufficient_net_r' if rejected_cost else 'no_fill_in_entry_window'}
    entry = D(entry_bar['open'])*(1+slip)
    result.update(entry_price=str(entry), entry_bar_open=entry_bar['open_time'], entry_net_r=str(net_r))
    deadline = entry_bar['open_time']+horizon_hours*3600000
    previous = entry_bar['open_time']
    adverse = favorable = D(0)
    for bar in rows:
        if bar['open_time'] < entry_bar['open_time']:
            continue
        if bar['open_time'] != previous or bar['close_time']-bar['open_time'] != duration:
            return {**result, 'status': 'missing_exit_history'}
        previous = bar['close_time']
        adverse = min(adverse, (D(bar['low'])/entry-1)*100)
        favorable = max(favorable, (D(bar['high'])/entry-1)*100)
        stop_hit, target_hit = D(bar['low']) <= stop, D(bar['high']) >= goal
        exit_price = None
        if stop_hit:
            exit_price = min(stop,D(bar['open']))
            reason = 'stop_first_ambiguous' if target_hit else 'stop'
        elif target_hit:
            exit_price, reason = goal, 'first_target'
        elif bar['close_time'] == deadline:
            exit_price, reason = D(bar['close']), 'time_exit'
        elif bar['close_time'] > deadline:
            return {**result, 'status': 'horizon_not_on_bar_boundary'}
        if exit_price is not None:
            exit_price *= 1-slip
            net = (exit_price-entry-(entry+exit_price)*fee-entry*funding)/entry*100
            return {**result, 'status': 'completed_cost_scenario', 'exit_reason': reason,
                    'exit_price': str(exit_price), 'exit_bar_close': bar['close_time'],
                    'intrabar_exit_time': None, 'net_return_pct': str(net),
                    'bar_extreme_mae_pct': str(adverse), 'bar_extreme_mfe_pct': str(favorable),
                    'excursion_kind': 'whole_exit_bar_bounds_not_exact_trade_path'}
    return {**result, 'status': 'right_censored_history'}
