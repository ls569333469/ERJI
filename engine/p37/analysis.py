"""Analysis of one observed market; no all-market barrier."""
from decimal import Decimal as D
from sqlalchemy import select,text
from engine.db import Session
from engine.models import Candle,Observation,UserPreference
from engine.repository import emit
from engine.features.numeric import relative_volume

STEPS={'5':300000,'15':900000,'30':1800000,'60':3600000,'240':14400000,'1D':86400000}
# Six extra source bars retain 300 complete 30m buckets at every 5m offset.
# The discarded partial first/last buckets never count as closed history.
SOURCE_5M_BARS=1806
def aggregate(rows,step):
    groups={}
    for r in rows:groups.setdefault(r['open_time']//step*step,[]).append(r)
    result=[]
    for start,g in sorted(groups.items()):
        g=sorted(g,key=lambda r:r['open_time'])
        if len(g)!=step//300000 or [r['open_time'] for r in g]!=list(range(start,start+step,300000)):continue
        result.append(dict(open_time=start,close_time=start+step,close=g[-1]['close'],volume=sum(r['volume'] for r in g) if all(r['volume'] is not None for r in g) else None))
    return result

def ema(values,length):
    value=sum(values[:length])/length;alpha=D(2)/D(length+1)
    for item in values[length:]:value=item*alpha+value*(1-alpha)
    return value

def trend(rows,step,now):
    if len(rows)<100:return dict(direction='unknown',status='warming',bars=len(rows))
    if any(b['open_time']-a['open_time']!=step for a,b in zip(rows,rows[1:])):return dict(direction='unknown',status='gap',bars=len(rows))
    if rows[-1]['close_time']<now//step*step:return dict(direction='unknown',status='delayed',bars=len(rows))
    values=[D(str(r['close'])) for r in rows];a=ema(values,50);b=ema(values,100);price=values[-1]
    direction='up' if price>a>b else 'down' if price<a<b else 'mixed'
    import talib,numpy as np
    rsi=float(talib.RSI(np.array([float(v) for v in values]),timeperiod=14)[-1])
    mid=sum(values[-20:])/20;sd=(sum((x-mid)**2 for x in values[-20:])/20).sqrt()
    multiple=relative_volume(rows[-1]['volume'],[r['volume'] for r in rows[-21:-1]],20,'mean')
    difs=macd_differences(values)
    return dict(direction=direction,status='ready' if len(rows)>=300 else 'provisional',bars=len(rows),as_of=rows[-1]['close_time'],ema50=str(a),ema100=str(b),rsi14=rsi,macd=str(difs[-1]),macd_signal=str(ema(difs,9)),boll_low=str(mid-2*sd),boll_high=str(mid+2*sd),rvol20=float(multiple) if multiple is not None else None)

def macd_differences(values):
    """Same SMA seed and Decimal recurrence as ema; linear instead of quadratic."""
    fast=ema(values[:26],12);slow=ema(values[:26],26);result=[fast-slow]
    af=D(2)/D(13);aslow=D(2)/D(27)
    for value in values[26:]:
        fast=value*af+fast*(1-af);slow=value*aslow+slow*(1-aslow)
        result.append(fast-slow)
    return result

def analyze(ident,now):
    with Session() as s:
        row=s.get(Observation,ident)
        if not row or row.status=='archived':return
        iid=row.instrument_id
    return analyze_market(iid,now)

def analyze_market(iid,now):
    import hashlib,json
    from copy import deepcopy
    from engine.p38.windows import read,token
    from engine.adapters.base import now_ms
    key='p39_market_trend:'+iid
    with Session() as s:
        s.execute(text("SET LOCAL statement_timeout='15s'"))
        rows=list(s.scalars(select(Observation).where(Observation.instrument_id==iid,Observation.visible_at.is_not(None),Observation.status!='archived')))
        ids=[r.id for r in rows]
        versions={r.id:r.version for r in rows}
        inputs={tf:token(s,iid,tf,now,limit=SOURCE_5M_BARS if tf=='5' else 300) for tf in ('5','60','240','1D')}
        signature=hashlib.sha256(json.dumps(['binance-market-trend-v2',inputs],sort_keys=True).encode()).hexdigest()
        cached=s.get(UserPreference,key)
        result=deepcopy(cached.payload) if cached and cached.payload.get('signature')==signature else None
        if result is None:
            data={tf:[dict(open_time=c['open_time'],close_time=c['close_time'],close=c['close'],volume=c['quote_volume'],revision=c['revision']) for c in read(s,iid,tf,now,SOURCE_5M_BARS if tf=='5' else 300)] for tf in inputs}
    computed=result is None
    if computed:
        data['15']=aggregate(data['5'],900000)[-300:];data['30']=aggregate(data['5'],1800000)[-300:]
        matrix={tf:trend(data[tf][-300:],step,now) for tf,step in STEPS.items()}
        missing=[tf for tf,t in matrix.items() if t['status']!='ready']
        label='周期分歧'
        for direction,short,medium,multi in [('up','短线转强','中期向上','多周期向上'),('down','短线转弱','中期向下','多周期向下')]:
            near=sum(matrix[tf]['direction']==direction for tf in ('5','15','30'))>=2
            mid=all(matrix[tf]['direction']==direction for tf in ('60','240'))
            if near and mid and matrix['1D']['direction']==direction:label=multi
            elif mid:label=medium
            elif near:label=short
        result={'signature':signature,'matrix':matrix,'trend_label':label,'missing_timeframes':missing,'analyzed_at':now,'analysis_status':'ready' if not missing else 'filling'}
    # Freshness is checked each review against the source close, without
    # recalculating indicators or assigning an old input a new compute time.
    for tf,item in result['matrix'].items():
        if item.get('as_of') and item.get('status') in ('ready','provisional','delayed'):
            item['status']=('ready' if item['bars']>=300 else 'provisional') if 0<=now-item['as_of']<STEPS[tf] else 'delayed'
    result['missing_timeframes']=[tf for tf,item in result['matrix'].items() if item['status']!='ready']
    result['analysis_status']='ready' if not result['missing_timeframes'] else 'filling'
    result['checked_at']=now
    def current(session):
        return all(token(session,iid,tf,now_ms(),limit=SOURCE_5M_BARS if tf=='5' else 300)==value for tf,value in inputs.items())
    with Session.begin() as s:
        s.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':key})
        if not current(s):return {'superseded':1}
        old=s.get(UserPreference,key,with_for_update=True)
        if old and old.updated_at>now:return {'superseded':1}
        s.merge(UserPreference(key=key,updated_at=now,payload=result))
    updated=0
    for offset in range(0,len(ids),32):
        with Session.begin() as s:
            if not current(s):return {'superseded':1}
            changed=[]
            for row in s.scalars(select(Observation).where(Observation.id.in_(ids[offset:offset+32])).order_by(Observation.id).with_for_update()):
                if row.status=='archived' or row.payload.get('analyzed_at',0)>now:continue
                if row.payload.get('analysis_signature')==signature and row.payload.get('analysis_checked_at',0)>=now:continue
                row.payload={**row.payload,'matrix':result['matrix'],'trend_label':result['trend_label'],
                    'analysis_signature':signature,'analyzed_at':result['analyzed_at'],'analysis_checked_at':now,
                    'analysis_source_version':versions.get(row.id,row.version),'analysis_status':result['analysis_status'],
                    'missing_timeframes':result['missing_timeframes'],'trend_next_retry_at':now+60000 if result['missing_timeframes'] else 0}
                row.version=max(now,row.version);changed.append(row.id);updated+=1
            if changed:emit(s,'observation_update',{'ids':changed,'version':now})
    return {'updated':updated,'computed':computed,'computed_at':result['analyzed_at'],
        'input_version':signature,'waiting_inputs':bool(result['missing_timeframes']),
        'missing_timeframes':result['missing_timeframes']}
