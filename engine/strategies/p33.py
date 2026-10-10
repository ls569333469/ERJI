"""Versioned technical recognition and tracking. Research reports are not publication gates.
Legacy version/hash identifiers are retained so frozen events remain comparable.
"""
from decimal import Decimal as D
from hashlib import sha256
import json
import numpy as np
import talib
from engine.pd2.analysis import closed_bars, DURATIONS
from engine.pd2.event_overlays import pivots
from engine.strategies.structures import volume_multiple

VERSION = "p33-price-shadow-v1"
PARAMETERS = {
    "buffer_atr": "0.10", "buffer_ticks": 2, "quote_volume_window": 20,
    "min_volume_multiple": "1.5", "entry_distance_atr": "1", "atr_period": 14,
    "recent_high_days": 30, "squeeze_lookback": 120, "squeeze_quantile": ".20",
    "squeeze_recent": 5, "squeeze_required": 3, "squeeze_boundary_bars": 20,
    "double_bottom_min_gap": 5, "double_bottom_tolerance_atr": ".5",
    "double_bottom_timeout": 60, "retest_timeout_4h": 6,
    "trend_touch_atr": ".25", "reclaim_timeout_4h": 3,
    "attempt_days": 45, "feature_days": 30, "attempt_gap": 3,
    "attempt_retracement_atr": "1", "attempt_tolerance_atr": ".5",
    "pivot_left": 2, "pivot_right": 2, "equal_pivot": "earliest",
    "ema": [20, 50], "macd": [12, 26, 9], "bb": [20, 2],
    "entry_lifetime_bars": 1, "invalidation": "closed_below_frozen_boundary_minus_buffer",
    "release": "shadow_only", "trading_enabled": False,
}
NAMES = {
    "recent_high": "近期前高突破", "ath": "历史新高突破", "retest": "突破后回踩",
    "squeeze": "收缩突破", "double_bottom": "双底突破", "early_strength": "多周期转强",
    "trend_pullback": "趋势回踩", "failed_breakdown": "假跌破收回", "repeated_test": "多轮试探再突破",
}
PRIORITY = ["ath", "repeated_test", "double_bottom", "squeeze", "recent_high", "retest",
            "failed_breakdown", "trend_pullback", "early_strength"]
CONFIG_HASH = sha256(json.dumps(PARAMETERS, sort_keys=True).encode()).hexdigest()[:16]


class Missing(ValueError):
    def __init__(self,message,code='dependency_unavailable'):
        super().__init__(message);self.code=code


class Facts:
    def __init__(self, bars, now, tick):
        self.rows = {tf: closed_bars(rows, now) for tf, rows in bars.items()}
        self.gaps=set()
        for tf, rows in self.rows.items():
            breaks=[i for i in range(1,len(rows)) if rows[i]['open_time']-rows[i-1]['open_time']!=DURATIONS[tf]]
            if breaks:self.gaps.add(tf);self.rows[tf]=rows[breaks[-1]:]
        self.now, self.tick, self.cache = now, D(str(tick)), {}
        if not self.tick.is_finite() or self.tick <= 0:
            raise ValueError("tick_size must be positive and finite")

    def get(self, tf, count, before=None):
        rows = self.rows.get(tf, [])
        if before is not None:
            closed_before=[b for b in rows if b['close_time']<=before]
            rows = [b for b in closed_before if b.get("received_at", 0) <= before]
            # Source freshness now and availability before an old trigger are
            # different facts. Backfilled history cannot rewrite that trigger.
            if len(closed_before)>=count and (len(rows)<count or before-rows[-1]['close_time']>DURATIONS[tf]+180000):
                raise Missing(f"{tf}历史现已补齐，但在原触发前尚未收到；等待新的闭合触发",'history_not_known_before_trigger')
        if len(rows) < count:
            if tf in self.gaps:raise Missing(f"{tf}窗口存在K线缺口，连续历史不足{count}根",'candle_gap')
            raise Missing(f"{tf}需要至少{count}根完整K线",'history_insufficient')
        if any(b['open_time']-a['open_time'] != DURATIONS[tf] for a, b in zip(rows[-count:], rows[-count+1:])):
            raise Missing(f"{tf}窗口存在K线缺口",'candle_gap')
        if (before if before is not None else self.now)-rows[-1]['close_time'] > DURATIONS[tf]+180000:
            raise Missing(f"{tf}闭合数据已过期",'closed_candles_stale')
        return rows

    def indicator(self, tf, name):
        key = (tf, name)
        if key not in self.cache:
            rows = self.rows.get(tf, [])
            values = lambda field: np.array([float(b[field]) for b in rows], dtype=float)
            c, h, l = values('close'), values('high'), values('low')
            if name == 'atr': out = talib.ATR(h, l, c, 14)
            elif name == 'ema20': out = talib.EMA(c, 20)
            elif name == 'ema50': out = talib.EMA(c, 50)
            elif name == 'hist': out = talib.MACD(c, 12, 26, 9)[2]
            elif name == 'bandwidth':
                u, m, l = talib.BBANDS(c, 20, 2, 2, 0)
                out = np.divide(u-l, m, out=np.full_like(m, np.nan), where=m>0)
            else: raise ValueError(name)
            self.cache[key] = out
        return self.cache[key]

    def number(self, tf, name, index=-2):
        value = self.indicator(tf, name)[index]
        if not np.isfinite(value): raise Missing(f"{tf} {name}预热不足")
        return D(str(value))


def boundary(price, known, start, label):
    return {"kind":"boundary", "price":str(price), "known_at":known, "start_time":start, "label":label}


def point(p, label):
    return {"kind":"pivot", "price":p['price'], "time":p['pivot_time'], "known_at":p['known_at'], "label":label}


def recognize(rule, facts, market_id, coverage=None, parent=None):
    output = {"strategy_id":rule, "strategy_version":VERSION, "config_hash":CONFIG_HASH,
              "label":NAMES[rule], "market_id":market_id, "as_of":facts.now,
              "status":"no_match", "reason":"尚未满足形态条件", "anchors":[],
              "required_indicators":[], "trading_enabled":False}
    try:
        tf = '60' if rule in ('early_strength','trend_pullback','failed_breakdown','retest') else '240'
        rows = facts.get(tf, 52); bar = rows[-1]; prior = rows[:-1]
        atr = facts.number(tf,'atr')
        if atr <= 0: raise Missing("ATR无效")
        buffer = max(facts.tick*2, atr*D(PARAMETERS['buffer_atr']))
        vm = volume_multiple(bar, prior)
        if vm is None: raise Missing("报价成交额缺失或基线无效")
        known = max(bar['close_time'],bar.get('received_at',0))
        anchors=[];low=None;structure_id=None;extra=True;source_tf=tf
        if rule == 'recent_high':
            days=facts.get('1D',30,before=bar['open_time'])[-30:]
            level=max(D(b['high']) for b in days)
            anchors=[boundary(level,days[-1]['close_time'],days[0]['open_time'],'30日冻结前高')]
            low=min(D(b['low']) for b in prior[-5:])
        elif rule == 'ath':
            coverage=coverage or {}
            if coverage.get('market_id')!=market_id or coverage.get('complete') is not True or coverage.get('as_of',facts.now+1)>bar['open_time']:
                raise Missing("缺少同市场完整可比历史覆盖证明")
            if coverage.get('end_time',0)<bar['open_time'] or coverage.get('start_time',bar['open_time'])>=bar['open_time']:
                raise Missing("历史覆盖未到达触发前")
            # Coverage must provide an immutable high computed across the whole declared range.
            level=D(str(coverage['high']))
            if not level.is_finite() or level<=0: raise Missing("历史高点无效")
            output['label']='合约历史新高突破' if coverage.get('scope')=='contract_since_listing' else '历史新高突破'
            anchors=[boundary(level,coverage['as_of'],coverage['start_time'],output['label'])]
            low=min(D(b['low']) for b in prior[-5:])
        elif rule == 'squeeze':
            facts.get(tf,145)
            bandwidth=facts.indicator(tf,'bandwidth')
            baseline=bandwidth[-126:-6]
            if len(baseline)!=120 or not np.isfinite(baseline).all(): raise Missing("布林带宽120根基线不足")
            q=float(np.quantile(baseline,.2,method='linear'))
            if sum(x<=q for x in bandwidth[-6:-1])<3: return output
            level=max(D(b['high']) for b in prior[-20:]);low=min(D(b['low']) for b in prior[-20:])
            anchors=[boundary(level,prior[-1]['close_time'],prior[-20]['open_time'],'整理上沿')]
            extra=bandwidth[-1]>bandwidth[-2]
            output['required_indicators']=['bb']
        elif rule == 'double_bottom':
            bottoms=pivots(prior,'low')
            if len(bottoms)<2: return output
            a,b=bottoms[-2:]
            anchor_atr=facts.number(tf,'atr',b['index']+2)
            if b['index']-a['index']<5 or abs(D(a['price'])-D(b['price']))>anchor_atr*D('.5'): return output
            if len(prior)-1-b['index']>60:return {**output,'status':'expired','reason':'双底候选已超过60根4H'}
            between=prior[a['index']+1:b['index']]
            if not between:return output
            level=max(D(x['high']) for x in between);low=min(D(a['price']),D(b['price']))
            anchors=[point(a,'第一低点'),point(b,'第二低点'),boundary(level,b['known_at'],a['pivot_time'],'颈线')]
            if any(D(x['low'])<low-anchor_atr*D('.5') for x in prior[b['index']+3:]):return output
        elif rule == 'early_strength':
            four=facts.get('240',52,before=bar['open_time']);bottoms=pivots(four,'low');tops=pivots(prior,'high')
            if len(bottoms)<2 or not tops:return output
            if D(bottoms[-1]['price'])<=D(bottoms[-2]['price']):return output
            if not (facts.number(tf,'hist',-1)>0>=facts.number(tf,'hist',-2)):return output
            top=tops[-1];level=D(top['price']);low=min(D(b['low']) for b in prior[-5:])
            anchors=[point(top,'确认前高'),boundary(level,top['known_at'],top['pivot_time'],'转强触发位')]
            output['required_indicators']=['macd']
        elif rule in ('trend_pullback','failed_breakdown'):
            four=facts.get('240',52,before=bar['open_time']);touch=four[-1];fp=four[:-1];source_tf='240'
            # Most recent 4H setup only, then at most three 4H for the separate 1H trigger.
            if bar['close_time']-touch['close_time']>3*DURATIONS['240']:return output
            index=len(four)-2;fa=facts.number('240','atr',index);e=max(2*facts.tick,fa*D('.1'))
            if rule=='trend_pullback':
                daily=facts.get('1D',52,before=touch['open_time']);tops=pivots(daily,'high');bottoms=pivots(daily,'low')
                if len(tops)<2 or len(bottoms)<2 or D(tops[-1]['price'])<=D(tops[-2]['price']) or D(bottoms[-1]['price'])<=D(bottoms[-2]['price']):return output
                di=len(daily)-1
                if facts.number('1D','ema20',di)<=facts.number('1D','ema50',di):return output
                ref=facts.number('240','ema20',index)
                if not (ref-fa*D('.25')<=D(touch['low'])<=ref+fa*D('.25') and D(touch['close'])>ref):return output
                output['required_indicators']=['ema']
            else:
                bottoms=pivots(fp,'low')
                if not bottoms:return output
                ref=D(bottoms[-1]['price'])
                if not (D(touch['low'])<ref-e and D(touch['close'])>ref+e):return output
            level=D(touch['high']);low=D(touch['low'])
            anchors=[boundary(ref,fp[-1]['close_time'],touch['open_time'],'回踩参考' if rule=='trend_pullback' else '原支撑'),
                     {"kind":"pivot","time":touch['open_time'],"known_at":touch['close_time'],"price":str(low),"label":"回踩/收回"}]
        elif rule=='retest':
            if not parent or parent.get('market_id')!=market_id or parent.get('status')!='triggered':raise Missing("缺少同市场已确认父突破事件")
            start=parent['trigger']['close_time'];level=D(parent['boundary']);pa=D(parent['atr'])
            if known-start>6*DURATIONS['240']:return {**output,'status':'expired','reason':'父突破回踩窗口已结束'}
            touched=[b for b in prior if b['open_time']>=start and D(b['low'])<=level+pa*D('.25') and D(b['close'])>=level-pa*D('.25')]
            if not touched:return output
            if any(D(b['close'])<level-pa*D('.25') for b in prior if b['open_time']>=start):return {**output,'status':'invalidated','reason':'父突破边界已失守'}
            low=min(D(b['low']) for b in touched)
            anchors=[boundary(level,start,start,'父突破位'),{'kind':'pivot','time':touched[0]['open_time'],'known_at':touched[0]['close_time'],'price':touched[0]['low'],'label':'实际回踩'}]
            output['parent_event_id']=parent['event_id'];structure_id=parent['event_id']
        elif rule=='repeated_test':
            days=facts.get('1D',45,before=bar['open_time'])[-45:];facts.get('240',271)
            level=max(D(b['high']) for b in days)
            start=days[0]['open_time'];tops=[p for p in pivots(prior,'high') if p['pivot_time']>=start and abs(D(p['price'])-level)<=atr*D('.5')]
            attempts=[]
            for p in tops:
                if not attempts:attempts.append(p);continue
                previous=attempts[-1]
                between=prior[previous['index']+1:p['index']]
                if p['index']-previous['index']>=3 and between and min(D(b['low']) for b in between)<=min(D(previous['price']),D(p['price']))-atr:
                    attempts.append(p)
            if len(attempts)<2:return output
            anchors=[boundary(level,days[-1]['close_time'],start,'45日事件高点')]+[point(p,'试探') for p in attempts]
            low=min(D(b['low']) for b in prior[attempts[-1]['index']:])
            output['feature_window_days']=30
        else:raise ValueError(rule)
        boundary_time=max(a['known_at'] for a in anchors)
        if boundary_time>bar['open_time']:raise Missing("结构直到触发K线开始后才可知")
        threshold=level+buffer
        output.update(status='waiting',reason='等待闭合突破与成交额确认',timeframe=tf,source_timeframe=source_tf,
                      boundary=str(level),threshold=str(threshold),atr=str(atr),volume_multiple=str(vm),anchors=anchors,
                      entry_zone=[str(threshold),str(level+atr)],protection=str(low-buffer),
                      invalidation={'timeframe':tf,'price':str(level-buffer),'condition':'close_below'},
                      expires_at=known+DURATIONS[tf],required_indicators=output['required_indicators'])
        if D(bar['close'])>threshold and vm>=D('1.5') and extra:
            if D(prior[-1]['close'])>threshold and rule not in ('early_strength','retest','trend_pullback','failed_breakdown'):
                return {**output,'status':'no_match','reason':'已在边界上方，非首次穿越'}
            output.update(status='triggered',reason='价格与成交额触发已确认',trigger={**bar,'resolution':tf},known_at=known)
            identity=[market_id,rule,VERSION,structure_id or [(a.get('time',a.get('start_time')),a.get('price')) for a in anchors],bar['close_time']]
            output['event_id']='p33-'+sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:24]
            output['anchors'].append({'kind':'trigger_bar','open_time':bar['open_time'],'close_time':bar['close_time'],'price':bar['close'],'timeframe':tf,'known_at':known})
        return output
    except (Missing, KeyError) as exc:
        return {**output,'status':'missing_data','reason':str(exc),'reason_code':getattr(exc,'code','dependency_field_missing')}


def evaluate(bars, now, tick, market_id, coverage=None, parent=None):
    facts=Facts(bars,now,tick)
    results=[recognize(rule,facts,market_id,coverage,parent) for rule in NAMES]
    hits=[r for r in results if r['status']=='triggered']
    hits.sort(key=lambda r: PRIORITY.index(r['strategy_id']))
    return {'version':VERSION,'config_hash':CONFIG_HASH,'market_id':market_id,'as_of':now,
            'main':hits[0] if hits else None,'auxiliary_hits':hits[1:],'results':results,
            'selection_reason':'固定优先级选择有完整证据的主形态；参与条件单独计算',
            'trading_enabled':False}
