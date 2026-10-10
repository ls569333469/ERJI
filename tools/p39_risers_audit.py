"""Read-only causal audit of the twelve Binance contracts in the user screenshot."""
import json, sys, time
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.p34_local import prepare
prepare()
from sqlalchemy import text,create_engine
from engine.config import settings

NOW=int(time.time()*1000)
SYMBOLS={'RLC':'60.72','BR':'45.90','CAP':'42.02','NMR':'33.84','ORCA':'25.65','US':'21.02','API3':'17.02','C98':'16.29','EDU':'12.54','ZRO':'12.15','VTHO':'11.45','RAYSOL':'10.69'}
result={'checked_at_bjt':datetime.now(timezone(timedelta(hours=8))).isoformat(),'as_of_ms':NOW,'read_only':True,
        'screenshot_pct_not_live_price':True,'rows':[]}
def slim(p,keys):return {k:p.get(k) for k in keys if k in p}
def event_summary(e):
    q=e.get('qualification') or {}; initial=e.get('initial') or {}; plan=e.get('plan') or {}
    return {'initial':slim(initial,('strategy_id','timeframe','known_at','trigger_at','trigger','entry_zone','protection','expires_at','parent_event_id')),
            'status':e.get('status'),'invalidated_at':e.get('invalidated_at'),'protection_observed_at':e.get('protection_observed_at'),
            'plan':slim(plan,('expires_at','entry_zone','original_protection','targets')),
            'qualification':slim(q,('status','rule_action','execution','reasons','reason_codes','evaluated_at'))}
db=create_engine(settings().database_url,isolation_level='AUTOCOMMIT',connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=8000 -c lock_timeout=1000'})
with db.connect() as c:
    def query(sql,**params):return [dict(r) for r in c.execute(text(sql),params).mappings()]
    active=query("select payload from user_preferences where key='p37_active'")
    selected=(active[0]['payload'] if active else {}).get('instrument_ids',[])
    result['active_slots']=selected
    result['priority_audit']=query("select count(distinct asset_id) protected_assets,count(distinct instrument_id) protected_markets from observations where visible_at is not null and status in ('observing','focused','cooling') and (payload->>'trade_status'='active' or payload->>'pinned'='true')")
    result['plan_priority_audit']=query("select count(distinct p.execution_instrument_id) phase_alive_markets,count(distinct p.execution_instrument_id) filter(where p.expires_at>:now) entry_alive_markets from plans p join episodes e on e.id=p.episode_id where e.phase not in ('ended','invalidated')",now=NOW)
    for symbol,pct in SYMBOLS.items():
        iid='binance:perp:'+symbol+'USDT'; row={'symbol':symbol,'instrument_id':iid,'screenshot_24h_pct':pct,'selected_for_deep':iid in selected}
        row['market']=query("select i.asset_id,i.native_symbol,i.quote_asset,i.spec,m.as_of,m.payload from instruments i left join market_states m on m.instrument_id=i.id where i.id=:iid",iid=iid)
        row['observations']=query("select id,direction,status,first_at,visible_at,last_at,version,payload from observations where instrument_id=:iid order by last_at desc limit 3",iid=iid)
        for obs in row['observations']:
            obs['payload']=slim(obs['payload'],('classification','classification_context','trigger_reasons','trigger_timeframe','analyzed_at','analysis_checked_at','analysis_status','structure_checked_at','strategy_results','strategies','missing_timeframes','blockers','reason','trade_status','price','price_at','visible_price','tracking_tier','signal_ids','buy_eligible'))
        row['tasks']=query("select kind,state,lease_until,payload from tasks where kind like 'p39_%' and payload->>'subject'=:iid order by kind",iid=iid)
        for task in row['tasks']:task['payload']=slim(task['payload'],('submitted_at','started_at','finished_at','completed_count','failed_count','error','retired_reason','queue_wait_ms','execution_ms','scanned','result'))
        prefs=query("select key,updated_at,payload from user_preferences where key=:key",key='p33_shadow:'+iid)
        row['p33']=[]
        for pref in prefs:
            p=pref['payload'];row['p33'].append({'updated_at':pref['updated_at'],'keys':list(p),'summary':p.get('summary'),'evaluations':{'error':p.get('result',{}).get('error'),'reason':p.get('result',{}).get('reason'),'results':[slim(v,('strategy_id','status','reason','reason_code','timeframe','entry_zone','protection','threshold')) for v in p.get('result',{}).get('results',[])]},
                                                'active_events':{k:event_summary(v) for k,v in p.get('active_events',{}).items()}})
        row['p33_error']=query("select updated_at,payload from user_preferences where key=:key",key='p33_shadow_error:'+iid)
        from engine.db import Session
        from engine.pd2.service import load_bars
        from engine.models import Instrument
        from engine.strategies.p33 import evaluate
        with Session() as s:
            s.execute(text('SET TRANSACTION READ ONLY'));s.execute(text("SET LOCAL statement_timeout='8s'"))
            inst=s.get(Instrument,iid)
            if inst:
                bars=load_bars(s,iid,NOW)
                fresh=evaluate(bars,NOW,inst.spec['tick_size'],iid)
                row['fresh_recognition']=[slim(v,('strategy_id','status','reason','reason_code','timeframe','entry_zone','threshold','volume_multiple','expires_at')) for v in fresh.get('results',[])]
        row['plans_summary']=query("select e.strategy_id,count(*) n,count(*) filter(where p.expires_at>:now) entry_unexpired,max(p.expires_at) latest_expiry from plans p join episodes e on e.id=p.episode_id where p.execution_instrument_id=:iid group by e.strategy_id",iid=iid,now=NOW)
        row['latest_plans']=query("select p.id,p.expires_at,e.strategy_id,e.phase,s.as_of,s.payload->'action' action,s.payload->'execution' execution,s.payload->'evidence' evidence,s.payload->'research_qualification' qualification from plans p join episodes e on e.id=p.episode_id left join lateral(select as_of,payload from signal_snapshots where plan_id=p.id order by as_of desc limit 1)s on true where p.execution_instrument_id=:iid order by p.expires_at desc limit 6",iid=iid)
        for plan in row['latest_plans']:
            plan['evidence']=slim(plan['evidence'] or {},('strategy_id','template','strategy_version','entry_zone','protection','expires_at','reason','trigger','threshold','blockers','status'))
            plan['qualification']=slim(plan['qualification'] or {},('status','rule_action','execution','reason_codes','reasons'))
        row['current']=query("select id,action,computed_at,valid_until,payload from current_signals where instrument_id=:iid order by computed_at desc limit 6",iid=iid)
        for current in row['current']:current['payload']=slim(current['payload'],('phase','reason','strategy_id','entry_zone','protection','targets','opportunity_level','opportunity_eligible','opportunity_eligibility_reason','preliminary_net_r','candidate_min_net_r'))
        row['actions_24h']=query("select s.payload->'action'->>'code' action,count(*) snapshots,count(distinct s.plan_id) plans,min(s.as_of) first_at,max(s.as_of) last_at from signal_snapshots s join plans p on p.id=s.plan_id where p.execution_instrument_id=:iid and s.as_of>=:start group by 1",iid=iid,start=NOW-86400000)
        row['first_buy_24h']=query("select s.id,s.plan_id,s.as_of,s.payload->'action' action,s.payload->'execution' execution from signal_snapshots s join plans p on p.id=s.plan_id where p.execution_instrument_id=:iid and s.as_of>=:start and s.payload->'action'->>'code' in ('trial','participate') order by s.as_of limit 1",iid=iid,start=NOW-86400000)
        row['candles']=query("select resolution,count(*) n,min(close_time) first_closed,max(close_time) latest_closed,max(received_at) latest_received from candles where instrument_id=:iid and is_closed and close_time<=:now group by resolution",iid=iid,now=NOW)
        result['rows'].append(row)
db.dispose()
path=ROOT/'docs/p39-validation'/('risers-chain-audit-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
path.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf8')
def summary(row):
    market=row['market'][0] if row['market'] else {};p=market.get('payload') or {}
    return {'symbol':row['symbol'],'market_price':p.get('price'),'market_as_of':market.get('as_of'), 'selected':row['selected_for_deep'],
            'observations':[{k:v for k,v in obs.items() if k != 'payload'}|{'analysis':slim(obs['payload'],('analysis_status','analyzed_at','structure_checked_at','missing_timeframes','trade_status'))} for obs in row['observations'][:1]],
            'p33_updated_at':row['p33'][0]['updated_at'] if row['p33'] else None,
            'p33_results':row['p33'][0]['evaluations'] if row['p33'] else None,'p33_error':row['p33_error'], 'tasks':row['tasks'],
            'latest_plan':[{k:v for k,v in plan.items() if k not in ('evidence','qualification','execution')} for plan in row['latest_plans'][:1]],
            'actions_24h':row['actions_24h'],'first_buy_24h':row['first_buy_24h'], 'fresh_recognition':row.get('fresh_recognition')}
print(json.dumps({'path':str(path),'priority_audit':result['priority_audit'],'plan_priority_audit':result['plan_priority_audit'],'rows':[summary(r) for r in result['rows']]},ensure_ascii=False,default=str))
