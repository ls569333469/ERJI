"""Pure status projections. Legacy evidence cannot masquerade as this run."""
import os
import json
import time
from pathlib import Path
from collections import Counter
from engine.p37.detector import Detector, PriceView

_run_cache = (0, None)

def current_run():
    run = os.getenv('ERJI_RUN_ID')
    if run:return run
    global _run_cache
    if time.monotonic()-_run_cache[0] < 30:return _run_cache[1]
    try:
        rows=json.loads((Path(__file__).resolve().parents[2]/'.runtime/processes.json').read_text('utf-8-sig'))
        ids={r.get('run_id') for r in rows if r.get('run_id')}
        run=next(iter(ids)) if len(ids)==1 else None
    except (OSError,ValueError,TypeError):run=None
    _run_cache=(time.monotonic(),run)
    return run

def run_scope(details, run):
    ident=(details or {}).get('run_id')
    return 'previous_run' if run and ident and run!=ident else 'current_run' if run and ident==run else 'unattributed'

LEGACY_SCAN=('last_full_scan_at','last_reconcile_batch_at','reconcile_cycle_complete',
    'eligible_markets','scanned','matched','stale','quote_stale','candles_stale','batch_size','last_batch_ms')

def scan_view(details):
    value=dict(details or {})
    legacy=dict(value.get('legacy_reconcile') or {})
    for key in LEGACY_SCAN:
        if key in value:legacy[key]=value.pop(key)
    old_reason=value.pop('reason',None)
    if old_reason and not value.get('event_scan'):legacy['reason']=old_reason
    event=value.get('event_scan')
    if event:
        value['reason']=event.get('reason','ok')
    elif value.get('last_event_scan_at'):
        value['reason']='legacy_event_unattributed'
    else:value['reason']='no_current_event_scan'
    if legacy:value['legacy_reconcile']={**legacy,'scope':'historical_reconcile_not_current_progress'}
    value['mode']='event_driven_structure'
    return value

def window_status(rows, at, window, tolerance=5000, max_age=15000, continuous=True, view=None):
    """Use the detector's endpoint rules, without extending either TTL."""
    rows=tuple(rows)
    if not rows:return 'latest_missing'
    if rows[-1][0]>at:return 'clock_invalid'
    if at-rows[-1][0]>max_age:return 'latest_stale'
    target=rows[-1][0]-window
    if target<rows[0][0]-tolerance:return 'history_missing'
    choices=[r for r in rows if abs(r[0]-target)<=tolerance]
    if not choices:return 'history_mismatch'
    first=min(choices,key=lambda r:abs(r[0]-target))
    if continuous:
        if (view or PriceView(rows,{})).at(at).delta(at,window,tolerance,max_age) is None:return 'interval_gap'
    elif Detector.delta(rows,at,window,tolerance,max_age) is None:return 'history_mismatch'
    return 'ready'

def window_metrics(meta, prices, oi, config, at, ready=None, statuses=None):
    eligible=[k for k,v in meta.items() if (v.get('liquidity') or 0)>=config['liquidity_min']]
    prices_result={}
    for minutes in (5,15):
        reasons=Counter(statuses[k][str(minutes)] if statuses is not None else 'ready' if ready is not None and ready[k][minutes] else window_status(prices.get(k,()),at,minutes*60000) for k in eligible)
        prices_result[str(minutes)]={'ready':reasons.pop('ready',0),'eligible':len(eligible),'unavailable':dict(reasons)}
    contracts=[k for k in eligible if meta[k].get('venue')=='binance']
    # OI endpoints need not have tick continuity. The signal still needs an
    # endpoint <=90s old; a second metric exposes collection freshness <=6m.
    reasons=Counter(window_status(oi.get(k,()),at,900000,60000,90000,False) for k in contracts)
    oi_result={'15':{'ready':reasons.pop('ready',0),'eligible':len(contracts),'unavailable':dict(reasons),
        'window_ms':900000,'endpoint_tolerance_ms':60000,'latest_max_age_ms':90000,
        'latest_within_6m':sum(bool(oi.get(k)) and 0<=at-oi[k][-1][0]<=360000 for k in contracts)}}
    return prices_result,oi_result

def event_id(iid, direction, reasons):
    from engine.p39.pipeline import digest
    # Source endpoint identity, not symbol, receipt time or observation row count.
    return digest([iid,direction,sorted((r['kind'],r['window'],r.get('source_from'),r['source_at'],r.get('value')) for r in reasons)])


def price_receipt_sample(meta, prices, receipts, config, at, limit=30, statuses=None):
    """Round-robin across eligibility, venue and both window failure reasons."""
    groups={};counts=Counter();rejected=Counter()
    for iid,m in meta.items():
        eligible=(m.get('liquidity') or 0)>=config['liquidity_min']
        status=statuses[iid] if statuses is not None else {str(w):window_status(prices.get(iid,()),at,w*60000) for w in (5,15)}
        key=(eligible,m.get('venue'),status['5'],status['15'])
        counts[key]+=1
        row=receipts.get(iid,{})
        rejected.update(row.get('rejections',{}))
        groups.setdefault(key,[]).append({'instrument_id':iid,'eligible':eligible,'venue':m.get('venue'),
            'liquidity_status':m.get('liquidity_status'),'window_status':status,**row})
    for rows in groups.values():rows.sort(key=lambda r:(r.get('last_accepted_source_at',0),r['instrument_id']))
    # All eligible strata are visited first; no global oldest-30 bias.
    keys=sorted(groups,key=lambda k:(not k[0],str(k[1:])));examples=[];turn=0
    while len(examples)<limit:
        batch=[groups[k][turn] for k in keys if len(groups[k])>turn]
        if not batch:break
        examples.extend(batch[:limit-len(examples)]);turn+=1
    return {'snapshot_at':at,'markets':len(receipts),'example_limit':limit,
        'scope':'stratified current snapshot; counters and maxima since process start',
        'rejection_counts':dict(rejected),'examples':examples,
        'strata':[{'eligible':k[0],'venue':k[1],'window_5m':k[2],'window_15m':k[3],
            'markets':n,'sampled':sum((r['eligible'],r['venue'],r['window_status']['5'],r['window_status']['15'])==k for r in examples)}
            for k,n in counts.items()]}
