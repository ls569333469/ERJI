"""Offline P33 recognizer audit. Reads archives only; never contacts exchanges."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine.strategies.p33 import evaluate, PARAMETERS, VERSION, CONFIG_HASH


def read(path, duration):
    rows=[]
    if not path.exists():return rows
    with path.open(encoding='utf-8-sig',newline='') as handle:
        for value in csv.DictReader(handle):
            start=int(value['timestamp'])
            rows.append({'open_time':start,'close_time':start+duration,'received_at':start+duration,
                         'is_closed':True,**{k:value[k] for k in ('open','high','low','close','quote_volume')},
                         'base_volume':value.get('base_volume') or None})
    return sorted(rows,key=lambda b:b['open_time'])


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-dir',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--symbols',nargs='+',default=['MOVR','NOM','RAVE','MYX','SIREN'])
    args=parser.parse_args()
    report={'version':VERSION,'config_hash':CONFIG_HASH,'parameters':PARAMETERS,'network_requests':0,
            'kind':'recognition_audit_not_profit_backtest','assumptions':['归档K线模拟在收盘时可用，未验证当时发布延迟','归档缺tick_size，本次不验证交易价格最小跳动','归档未证明完整上市历史，不认定ATH','缺1H历史的入口保持缺数据'],
            'release_enabled':False,'symbols':{}}
    for symbol in args.symbols:
        four=read(args.source_dir/(symbol+'_4h.csv'),14400000)
        daily=read(args.source_dir/(symbol+'_daily.csv'),86400000)
        counts=Counter();hits=[]
        for index,bar in enumerate(four):
            if index<51 or bar['close_time']<four[-1]['close_time']-30*86400000:continue
            now=bar['close_time']
            result=evaluate({'240':four[:index+1],'1D':[b for b in daily if b['close_time']<=now]},now,'0.00000001','binance:perp:'+symbol+'USDT')
            for item in result['results']:
                counts[item['strategy_id']+':'+item['status']]+=1
                if item['status']=='triggered':hits.append(item)
        report['symbols'][symbol]={'bars_4h':len(four),'bars_1d':len(daily),'coverage_start':four[0]['open_time'] if four else None,
            'coverage_end':four[-1]['close_time'] if four else None,'counts':dict(counts),'hits':hits}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({s:{'hits':len(v['hits']),'bars_4h':v['bars_4h']} for s,v in report['symbols'].items()},ensure_ascii=False))


if __name__=='__main__':main()
