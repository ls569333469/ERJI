"""Browser-only OHLC fixtures; never persisted to market or signal tables."""
from pathlib import Path
import sys,json
from copy import deepcopy
from decimal import Decimal as D
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from p33_chart_fixtures import run
from engine.strategies.p33_lifecycle import freeze_plan
from engine.strategies.p33_delivery import make_snapshot
from engine.strategies.p33_runtime import inherit_protection


def fixtures():
    result=run();original=result['recent_high']['snapshot'];hit=deepcopy(original['price_pattern'])
    data=deepcopy(original['candles_at_snapshot']);start=original['as_of'];step=14400000
    event={'initial':hit,'plan':freeze_plan(hit,data,start,'.001'),'first_observed_at':start,
        'status':'triggered','protection_history':[{'price':hit['protection'],'observed_at':start}]}
    for i,p in enumerate([12,11,10.5,11.5,12,14]):
        data['240'].append({'open_time':start+i*step,'close_time':start+(i+1)*step,
            'received_at':start+(i+1)*step,'is_closed':True,'open':str(p-.1),'high':str(p+.3),
            'low':str(p-.3),'close':str(p),'base_volume':'100','quote_volume':str(p*100)})
    inherit_protection(event,D(hit['protection'])+D('.1'),start+2*step)
    inherit_protection(event,D(hit['protection'])+D('.2'),start+4*step)
    snap=make_snapshot(event,data,{'instrument_id':hit['market_id'],'symbol':'算法验收样例',
        'price':'14','quote_asset':'USDT'},'.001',start+6*step,overlays=original['event_overlays'])
    assert snap['event_overlays']['fibonacci']['status']=='extension'
    assert len(snap['drawing_contract']['protection_history'])==3
    result['fib_c_and_protection_steps']={'kind':'controlled_algorithm_fixture_not_live_market','snapshot':snap}
    return result


if __name__=='__main__':
    out=Path('docs/p33-validation/remaining-20261002');out.mkdir(parents=True,exist_ok=True)
    result=fixtures();(out/'chart-fixtures.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    print('controlled fixtures:',len(result))
