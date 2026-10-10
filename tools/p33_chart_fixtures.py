"""Isolated chart acceptance fixtures. Never inserted into the live database."""
from pathlib import Path
import sys,json,inspect
from copy import deepcopy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
import test_p33_patterns as fixtures
from engine.strategies import p33
from engine.strategies.p33_lifecycle import freeze_plan
from engine.strategies.p33_delivery import make_snapshot


def run():
    found={};original=p33.recognize
    def capture(rule,facts,market,*args,**kwargs):
        result=original(rule,facts,market,*args,**kwargs)
        if result['status']=='triggered' and rule not in found:
            event={'initial':deepcopy(result),'plan':freeze_plan(result,facts.rows,facts.now,str(facts.tick)),
                   'first_observed_at':facts.now,'status':'triggered'}
            snap=make_snapshot(event,facts.rows,{'instrument_id':market,'symbol':'算法验收样例','quote_asset':'USDT','price':result['trigger']['close']},str(facts.tick),facts.now)
            found[rule]={'kind':'controlled_algorithm_fixture_not_live_market','snapshot':snap}
        return result
    try:
        fixtures.recognize=p33.recognize=capture
        for name,test in vars(fixtures).items():
            if name.startswith('test_') and callable(test) and not inspect.signature(test).parameters:test()
    finally:fixtures.recognize=p33.recognize=original
    assert set(found)==set(p33.NAMES),(set(p33.NAMES)-set(found))
    return found


if __name__=='__main__':
    dest=Path('docs/p33-validation/completion-20261002');dest.mkdir(parents=True,exist_ok=True)
    result=run();(dest/'chart-fixtures.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    print('controlled strategy fixtures:',','.join(result))
