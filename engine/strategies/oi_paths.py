"""Price/OI explanations on aligned closed 1D intervals, never entry gates."""
from decimal import Decimal as D, InvalidOperation

VERSION = 'p33-oi-paths-v1'


def classify(days, quantity_points, as_of, max_endpoint_age=300000):
    result={'version':VERSION,'timeframe':'1D','status':'missing_data','path':None,
            'reason':'同窗价格或数量OI端点不足','action_impact':'explanation_only','windows':[]}
    rows=sorted([b for b in days if b.get('is_closed') and b['close_time']<=as_of and b.get('received_at',b['close_time'])<=as_of],key=lambda b:b['open_time'])[-2:]
    if rows and as_of-rows[-1]['close_time']>86400000+180000:
        return {**result,'status':'stale','reason':'最近完整日线已过期'}
    points=sorted([p for p in quantity_points if p['time']<=as_of and p.get('received_at',p['time'])<=as_of],key=lambda p:p['time'])
    def endpoint(at):
        # A missing most recent sample stays missing; do not skip backwards over it.
        p=next((p for p in reversed(points) if p['time']<=at),None)
        if not p or at-p['time']>max_endpoint_age or p.get('quantity') is None:return None
        try:
            q=D(str(p['quantity']))
            return q if q.is_finite() and q>0 else None
        except InvalidOperation:return None
    for bar in rows:
        if bar['close_time']-bar['open_time']!=86400000:continue
        start,end=endpoint(bar['open_time']),endpoint(bar['close_time'])
        if start is None or end is None:continue
        result['windows'].append({'start_at':bar['open_time'],'end_at':bar['close_time'],
            'price_change_pct':str((D(bar['close'])/D(bar['open'])-1)*100),
            'quantity_change_pct':str((end/start-1)*100)})
    if not rows or not result['windows'] or result['windows'][-1]['end_at']!=rows[-1]['close_time']:return result
    last=result['windows'][-1];price=D(last['price_change_pct']);quantity=D(last['quantity_change_pct'])
    path='增仓上涨' if price>0 and quantity>0 else '减仓上涨' if price>0 and quantity<0 else None
    if len(result['windows'])==2:
        first=result['windows'][0]
        if first['end_at']==last['start_at'] and D(first['price_change_pct'])>0 and D(first['quantity_change_pct'])>0 and path=='减仓上涨':
            path='增仓转减仓的上涨延续'
    result.update(status='matched' if path else 'no_match',path=path,
                  reason='价格与数量OI同窗变化；不能据此确认轧空或操盘者' if path else '未满足上涨量仓路径')
    return result


def compare_case(current, historical):
    """Evidence comparison only. Neither absolute market size nor a fake percentage."""
    if current.get('status')!='triggered' or historical.get('status')!='triggered':return None
    if current.get('strategy_id')!=historical.get('strategy_id') or current.get('config_hash')!=historical.get('config_hash'):return None
    if current.get('strategy_version')!=historical.get('strategy_version'):return None
    if current.get('timeframe')!=historical.get('timeframe'):return None
    if current.get('source_timeframe',current.get('timeframe'))!=historical.get('source_timeframe',historical.get('timeframe')):return None
    differences=[{'metric':'报价成交额量比','current':current.get('volume_multiple'),'historical':historical.get('volume_multiple')}]
    for label,fn in [('突破距边界/ATR',lambda h:(D(h['trigger']['close'])-D(h['boundary']))/D(h['atr'])),('结构风险/ATR',lambda h:(D(h['threshold'])-D(h['protection']))/D(h['atr']))]:
        try:differences.append({'metric':label,'current':str(fn(current)),'historical':str(fn(historical))})
        except (KeyError,ArithmeticError):pass
    return {'status':'same_rule_evidence','similarities':[current['label']+'的价格结构和闭合成交额条件均满足'],
            'differences':differences,
            'missing':['未统一比较市场环境、历史同期流通市值和OI路径'],
            'case_event_id':historical.get('event_id'),'similarity_pct':None,'rise_probability':None}
