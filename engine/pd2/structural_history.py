"""Closed native structural history, separate from tick/trigger indicator windows.

No HTTP, no rewriting frozen plans. Coverage is bounded and explicitly reported;
missing history is not an ATH claim or a buy veto.
"""
from collections import OrderedDict
from copy import deepcopy
from threading import RLock
from hashlib import sha256
from decimal import Decimal as D
import json
from engine.pd2.analysis import structural, DURATIONS
from engine.p38.windows import token, read

LIMITS = {'240': 20000, '1D': 4000, '1W': 1000}
VERSION = 'native-structural-history-v1'
_cache = OrderedDict()
_lock = RLock()


def load(session, instrument, now, config, fallback=None):
    database = str(session.bind.url) if hasattr(session.bind, 'url') else str(id(session.bind))
    settings = (config.pivot_left, config.pivot_right, str(config.zone_atr_multiplier))
    result = {}
    for tf, limit in LIMITS.items():
        revision = token(session, instrument.id, tf, now, limit=limit)
        recent = (fallback or {}).get(tf, [])
        recent_revision = sha256(json.dumps(recent, sort_keys=True, default=str).encode()).hexdigest() if recent else None
        key = (database, instrument.id, tf, instrument.spec.get('tick_size'), settings, revision, recent_revision)
        with _lock:
            value = _cache.get(key)
            if value is not None: _cache.move_to_end(key)
        if value is None:
            rows = read(session, instrument.id, tf, now, limit)
            by_time = {b['open_time']: b for b in rows}
            for bar in recent:
                if bar['open_time'] not in by_time and bar['is_closed']: by_time[bar['open_time']] = bar
            rows = [by_time[t] for t in sorted(by_time)][-limit:]
            value = structural(rows, tf, now, instrument.spec.get('tick_size') or '0.00000001', config)
            value['coverage'].update(limit=limit, limit_reached=len(rows) >= limit,
                listing_at=instrument.spec.get('onboard_time'), native_market_id=instrument.id,
                revision=list(revision), history_policy=VERSION)
            with _lock:
                _cache[key] = value
                while len(_cache) > 64: _cache.popitem(last=False)
        value = deepcopy(value)
        if value.get('status') in ('fresh', 'stale'):
            value['status'] = 'fresh' if 0 <= now-value['closed_at'] <= DURATIONS[tf]+180000 else 'stale'
        result[tf] = value
    return result


def merge(assessment, context, reference=None, max_levels=None):
    """Keep indicator/visible candle windows; share structural facts and provenance."""
    if context is None: return assessment
    result = {**assessment, **{key: deepcopy(context[key]) for key in
        ('levels', 'excluded_levels', 'coverage', 'structural_version', 'pivot_tie_rule')},
        'structural_status': context['status']}
    if max_levels is not None and reference is not None:
        # Candidate summaries must not serialize thousands of lifetime levels
        # for every waiting template. Target selection already used the full set.
        price=D(str(reference));levels=result['levels']
        result['levels']=sorted(levels,key=lambda v:min(abs(D(v['low'])-price),abs(D(v['high'])-price)))[:max_levels]
        result['excluded_levels']=sorted(result['excluded_levels'],key=lambda v:v['known_at'],reverse=True)[:max_levels]
        result['coverage'].update(reference_levels_total=len(levels),reference_levels_returned=len(result['levels']))
    return result
