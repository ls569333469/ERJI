"""Read-only, causally timed outcome audit of actually observed P33 events."""
from pathlib import Path
import sys,json,time
from collections import Counter,defaultdict
from sqlalchemy import select
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[4]/'.env');load_dotenv(Path(__file__).resolve().parents[1]/'.env',override=True)
from engine.db import Session,engine
from engine.models import RawEvent,Candle
from engine.pd2.service import candle_dict
from engine.strategies.p33_replay import simulate,POLICY,CONFIG_HASH


def run():
    if engine.url.host not in ('localhost','127.0.0.1'):raise RuntimeError('local_only')
    now=int(time.time()*1000);events=[];counts=Counter();metrics=defaultdict(list)
    with Session() as session:
        source=list(session.scalars(select(RawEvent).where(RawEvent.source=='erji',RawEvent.endpoint=='p33_first').order_by(RawEvent.received_at)))
        for raw in source:
            event=raw.payload['event'];hit=event['initial']
            if not event.get('plan'):counts['legacy_plan_not_frozen']+=1;continue
            end=event['plan']['expires_at']+max(POLICY['horizons_hours'])*3600000
            candles=session.scalars(select(Candle).where(Candle.instrument_id==hit['market_id'],Candle.resolution==hit['timeframe'],
                Candle.open_time>=event['plan']['as_of']//{'60':3600000,'240':14400000}[hit['timeframe']]*{'60':3600000,'240':14400000}[hit['timeframe']],
                Candle.close_time<=min(end,now),Candle.received_at<=now,Candle.is_closed.is_(True)).order_by(Candle.open_time))
            rows=[candle_dict(c) for c in candles]
            for horizon in POLICY['horizons_hours']:
                result=simulate(event,rows,horizon)
                duration={'60':3600000,'240':14400000}[hit['timeframe']]
                expected=((max(hit['known_at'],event['plan']['as_of'])+duration-1)//duration)*duration
                if result['status']=='missing_entry_history' and now<expected+duration:
                    result['status']='pending_first_complete_entry_bar'
                counts[result['status']]+=1
                events.append({'market_id':hit['market_id'],'first_observed_at':event['first_observed_at'],**result})
                if result['status']=='completed_cost_scenario':metrics[str(horizon)].append(float(result['net_return_pct']))
    return {'as_of':now,'policy':POLICY,'config_hash':CONFIG_HASH,'network_requests':0,'read_only':True,
            'first_events':len(source),'scenario_counts':dict(counts),'scenarios':events,
            'completed_scenario_metrics':{h:{'count':len(v),'mean_net_return_pct':sum(v)/len(v)} for h,v in metrics.items()},
            'limitations':['仅实际首次观测后可用数据；缺目标、未成交、未成熟不填零收益',
                          '固定成本模拟，不代表账户成交；同根保护优先，退出整根极值为边界',
                          '同结构多模板并非独立交易样本；未通过分组去重、样本外和失败/下架审核前不能发布']}


if __name__=='__main__':
    path=Path('docs/p33-validation/completion-20261002/forward-outcomes.json');path.parent.mkdir(parents=True,exist_ok=True)
    result=run();path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('scenarios','policy')},ensure_ascii=False))
