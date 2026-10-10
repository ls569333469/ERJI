"""Offline archive audit of grouping, frozen plans, follow-up and fixed cost scenarios."""
import argparse
from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.p33_replay import read
from engine.strategies.p33 import VERSION, CONFIG_HASH
from engine.strategies.p33_lifecycle import group_hits, freeze_plan, follow, VERSION as PLAN_VERSION
from engine.strategies.p33_replay import simulate, POLICY, CONFIG_HASH as REPLAY_HASH


def run(source, recognition):
    if recognition.get('version') != VERSION or recognition.get('config_hash') != CONFIG_HASH:
        raise ValueError('recognition_version_mismatch')
    output={'kind':'frozen_plan_and_cost_scenario_audit','plan_version':PLAN_VERSION,
            'recognition_version':VERSION,'recognition_config_hash':CONFIG_HASH,
            'cost_policy':POLICY,'cost_config_hash':REPLAY_HASH,'network_requests':0,'trading_enabled':False,
            'limitations':['沿用归档模拟收盘即用和缺历史tick假设，不冒充当时实时信号',
                          '手续费/滑点/资金费均为冻结研究情景，不是账户实际成本',
                          '仅有事后选择的五币样本，不满足样本外、失败及下架样本门槛',
                          '同根保护与目标按保守保护优先；MAE/MFE含退出整根极值，不是精确成交路径'],
            'symbols':{}}
    for symbol,record in recognition['symbols'].items():
        bars={tf:read(source/(symbol+'_'+suffix+'.csv'),duration) for tf,suffix,duration in
              [('240','4h',14400000),('1D','daily',86400000),('60','1h',3600000),('1W','weekly',604800000)]}
        source_hashes={suffix:sha256((source/(symbol+'_'+suffix+'.csv')).read_bytes()).hexdigest()
                       for suffix in ('4h','daily','1h','weekly') if (source/(symbol+'_'+suffix+'.csv')).exists()}
        groups=group_hits(record['hits']);reports=[];statuses=Counter()
        for group_id,hits in groups.items():
            hit=hits[0];now=hit['known_at']
            event={'initial':deepcopy(hit),'first_observed_at':now,'status':'triggered',
                   'plan':freeze_plan(hit,bars,now,'0.00000001'),'trading_enabled':False}
            followed=event
            for bar in bars.get(hit['timeframe'],[]):
                if bar['close_time']>now:
                    followed=follow(followed,{hit['timeframe']:[bar]},bar['close_time'])
            scenarios=[simulate(event,bars.get(hit['timeframe'],[]),h) for h in POLICY['horizons_hours']]
            statuses.update(result['status'] for result in scenarios)
            reports.append({'group_id':group_id,'main_strategy':hit['strategy_id'],
                'auxiliary_strategies':[r['strategy_id'] for r in hits[1:]],'plan':event['plan'],
                'tracking':{k:v for k,v in followed.items() if k not in ('initial','plan')},'scenarios':scenarios})
        output['symbols'][symbol]={'source_sha256':source_hashes,'recognized_hits':len(record['hits']),
            'structure_groups':len(groups),'cost_scenario_statuses':dict(statuses),'groups':reports}
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source-dir',type=Path,required=True)
    parser.add_argument('--recognition',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=run(args.source_dir,json.loads(args.recognition.read_text(encoding='utf-8')))
    result['recognition_sha256']=sha256(args.recognition.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({symbol:{k:v for k,v in data.items() if k!='groups' and k!='source_sha256'} for symbol,data in result['symbols'].items()},ensure_ascii=False))
