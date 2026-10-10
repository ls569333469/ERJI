"""Cheap local capacity and queue diagnostics; no provider calls."""
from sqlalchemy import select,func
from engine.models import Instrument,UserPreference,CurrentSignal
from engine.pd2.request_budget import budget_spec

def capacity(session,now):
    modern=session.get(UserPreference,'p36_http_ledger')
    markets=dict(session.execute(select(Instrument.venue,func.count()).group_by(Instrument.venue)).all())
    prefs=list(session.scalars(select(UserPreference).where(UserPreference.key.startswith('http_budget:',autoescape=True))))
    queues=[]
    for row in prefs:
        if modern and row.key=='http_budget:binance_family':
            # Retained migration evidence also serves unverified products; it
            # must not be presented as the active UM/spot weight budget.
            source='binance_unverified_legacy'
        else:source=row.key.split(':',1)[1]
        p=row.payload;pending=[v for v in p.get('pending',[]) if now-v['seen_at']<90000]
        entries=[v for v in p.get('entries',[]) if now-v[0]<60000];used=sum(v[1] for v in entries)
        queues.append({'source':source,'budget':p.get('budget_cap'), 'used':used,
            'remaining':max(0,p.get('budget_cap',0)-used),'queued':len(pending),
            'oldest_wait_seconds':max([0]+[(now-v['enqueued_at'])//1000 for v in pending]),
            'cold_queued':sum(v.get('lane')=='cold' for v in pending),'blocked_until':p.get('blocked_until'),
            'cold_budget_pct':15,'registry':'legacy_unverified_or_other_provider'})
    from engine.pd2.p36_budget import KEY,LIMITS,used,clean
    row=session.get(UserPreference,KEY)
    if row:
        ledger=clean(row.payload,now)
        for family,(cap,window) in LIMITS.items():
            amount=used(ledger,family,now)
            pending=[p for p in ledger['pending'] if p['family']==family]
            queues.append({'source':'binance_'+family,'budget':cap,'used':amount,
                'remaining':max(0,cap-amount),'window_ms':window,'queued':len(pending),
                'oldest_wait_seconds':max([0]+[(now-p['enqueued'])//1000 for p in pending]),
                'blocked_until':max(ledger.get('binance_ban_until',0),ledger.get('migration_until',0)),
                'lane_weights':{'live':6,'repair':3,'cold':1},'registry':ledger['version']})
    signals=list(session.scalars(select(CurrentSignal)))
    active=len({r.instrument_id for r in signals if r.action in ('participate','trial')})
    candidates=len({r.instrument_id for r in signals if r.action in ('waiting','no_chase') and r.payload.get('opportunity_eligible')})
    count=markets.get('binance',0)
    endpoints=[]
    from engine.p39.priority import market_tiers
    from engine.pd2.collection_schedule import collection_interval
    from engine.p39.pipeline import enabled
    t2,t3,_=market_tiers(session,now) if enabled() else (set(),set(),set())
    native_ids=set(session.scalars(select(Instrument.id).where(Instrument.venue=='binance')))
    tier_counts={'ordinary':len(native_ids-t2-t3),'t2':len(native_ids & t2),'t3':len(native_ids & t3)}
    for endpoint,kind,fallback,batch in [('openInterest','oi',None,1),('premiumIndex',None,300,count or 1),('klines',None,14400,1),('fundingRate','funding_history',None,1)]:
        intervals={tier:collection_interval(kind,tier)//1000 if kind else fallback for tier in tier_counts}
        _,weight,cap,_=budget_spec('https://fapi.binance.com/fapi/v1/'+endpoint,{'params':{'limit':300}})
        endpoints.append({'source':'Binance','endpoint':endpoint,'weight':weight,'batch_size':batch,
            'markets':count,'interval_seconds':intervals['ordinary'],
            'ordinary_weight_per_minute':round(tier_counts['ordinary']/batch*60/intervals['ordinary']*weight,2),
            'target_weight_per_minute':round(sum(n/batch*60/intervals[tier]*weight for tier,n in tier_counts.items()),2),
            'tier_markets':tier_counts,'tier_interval_seconds':intervals,
            'active_markets':active,'candidate_markets':candidates,
            'active_interval_seconds':intervals['t3'],'candidate_interval_seconds':intervals['t2'],
            'shared_budget':cap,'scope':'target estimate; native T2/T3 tiers; signal action counts do not grant priority; queues are actual ledger observations'})
    rows=signals
    blockers={}
    for r in rows:
        reasons=set([r.payload.get('opportunity_eligibility_reason','unknown')])
        if r.payload.get('oi_cap_ratio') is None:reasons.add('oi_cap_missing')
        if r.action in ('paused','expired','ended'):reasons.add(r.action)
        for reason in reasons:blockers[reason]=blockers.get(reason,0)+1
    return {'as_of':now,'queues':queues,'endpoints':endpoints,'blockers':blockers,'event_count':len(rows),
        'counting':'同一事件可能有多个原因，不能相加当作代币数',
        'degradation':'已核实币安接口使用官方90%额度；实时/近期补洞/远期历史按6:3:1调度并按等待时间提升，空闲份额可借用。其他来源维持原限制。目标频率不代表实际覆盖。'}
