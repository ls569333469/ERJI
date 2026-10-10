"""Candidate admission audit; frozen historical trading rules stay unchanged."""
from decimal import Decimal as D, InvalidOperation
from hashlib import sha256
import json

VERSION = 'box-candidate-integrity-v1'
STEP = 14400000


def audit(box, bars):
    result = {'version': VERSION, 'status': 'unverified', 'reason': '箱体来源K线不足，暂停作为待突破候选'}
    try:
        start, end, count = box['start_time'], box['end_time'], box['lookback']
        upper, lower, atr, trigger = (D(str(box[k])) for k in ('upper', 'lower', 'atr', 'trigger_price'))
        if count not in (20, 60) or not all(v.is_finite() for v in (upper, lower, atr, trigger)) or not 0 < lower < upper < trigger or atr <= 0:
            return result
        sample = sorted((b for b in bars if start <= b['open_time'] and b['close_time'] <= end and b['is_closed']), key=lambda b: b['open_time'])
        if len(sample) != count or sample[0]['open_time'] != start or sample[-1]['close_time'] != end:
            return result
        if any(b['open_time'] != start+i*STEP or b['close_time'] not in (b['open_time']+STEP, b['open_time']+STEP-1) for i,b in enumerate(sample)):
            return result
        # Reuse the existing 0.25 ATR trigger buffer and 0.5 ATR touch tolerance.
        # A strong completed excursion cannot be buried inside a pending box.
        violations=[]
        for b in sample:
            high, low, close = (D(str(b[k])) for k in ('high','low','close'))
            if not all(v.is_finite() for v in (high,low,close)) or not 0 < low <= close <= high:
                return result
            if close > trigger or close < lower-atr*D('.25'):
                violations.append({'time':b['open_time'],'kind':'close_outside','price':str(close)})
            elif high > upper+atr*D('.5') or low < lower-atr*D('.5'):
                violations.append({'time':b['open_time'],'kind':'excursion_outside','high':str(high),'low':str(low)})
        source=[{k:b.get(k) for k in ('open_time','close_time','high','low','close','revision')} for b in sample]
        result.update(status='rejected' if violations else 'verified',
            reason='整理区间含明显越界行情，不能作为完整待突破箱体' if violations else '来源K线与冻结边界核验通过',
            sample_count=len(sample),last_open_time=sample[-1]['open_time'],
            source_hash=sha256(json.dumps(source,sort_keys=True).encode()).hexdigest(),
            violations=violations,upper_tolerance=str(upper+atr*D('.5')),lower_tolerance=str(lower-atr*D('.5')))
        return result
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return result
